"""Reconcile Art state without waking the TV; push updates between polls."""

import logging
from datetime import timedelta

from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .frame import FrameError


def ipc_issue_id(entry):
    return f"ip_control_unauthorized_{entry.entry_id}"


class ArtCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, controller):
        super().__init__(
            hass,
            logging.getLogger(__name__),
            name="Samsung Frame Art",
            config_entry=entry,
            update_interval=timedelta(seconds=15),
        )
        self.controller = controller
        self._refresh_task = None
        entry.async_on_unload(self.cancel_refresh)
        controller.on_update = self.push_update

    def schedule_refresh(self):
        """Keep a mode button responsive while discovering slower settings."""
        if not self._refresh_task or self._refresh_task.done():
            self._refresh_task = self.hass.async_create_task(
                self.async_request_refresh()
            )

    def cancel_refresh(self):
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()

    def push_update(self):
        # async_set_updated_data resets the polling timer. Frequent Art events
        # would then prevent the independent power check from ever running.
        self.controller.remember_mode()
        self.data = self.controller.controls
        self.async_update_listeners()

    async def _async_update_data(self):
        try:
            await self.controller.async_refresh()
        except FrameError as err:
            self.controller.clear_audio()
            self._update_ipc_issue()
            raise UpdateFailed(f"Art API unavailable ({err.code})") from None
        self.controller.remember_mode()
        # Volume is a separate local protocol; its failure only clears volume.
        await self.controller.async_refresh_audio()
        self._update_ipc_issue()
        return self.controller.controls

    def _update_ipc_issue(self):
        """Ask for re-pairing when the TV rejects the IP Control token."""
        if self.controller.ipc is None:
            return
        issue_id = ipc_issue_id(self.config_entry)
        if self.controller.ipc_auth_failed:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="ip_control_unauthorized",
                translation_placeholders={"name": self.config_entry.title},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
