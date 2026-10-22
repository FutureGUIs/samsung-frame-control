"""Regression fixtures for HA's exact SSDP manifest matching semantics.

HA's IntegrationMatchers uses dictionary-item inclusion, not fnmatch:
https://github.com/home-assistant/core/blob/dev/homeassistant/components/ssdp/scanner.py
These tests validate the packaged manifest, which config-flow unit tests bypass.
"""

import json
import unittest
from pathlib import Path

MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "custom_components/samsung_frame_control/manifest.json"
)


class DiscoveryManifestTests(unittest.TestCase):
    def matches(self, advertisement):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        return any(
            advertisement.items() >= matcher.items() for matcher in manifest["ssdp"]
        )

    def test_samsung_specific_services_match_without_manufacturer(self):
        for service in ("RemoteControlReceiver", "MainTVAgent2"):
            kind = "device" if service == "RemoteControlReceiver" else "service"
            self.assertTrue(self.matches({"st": f"urn:samsung.com:{kind}:{service}:1"}))

    def test_rendering_control_matches_actual_samsung_manufacturers(self):
        for manufacturer in ("Samsung", "Samsung Electronics"):
            self.assertTrue(
                self.matches(
                    {
                        "st": "urn:schemas-upnp-org:service:RenderingControl:1",
                        "manufacturer": manufacturer,
                    }
                )
            )

    def test_other_renderers_do_not_match(self):
        self.assertFalse(
            self.matches(
                {
                    "st": "urn:schemas-upnp-org:service:RenderingControl:1",
                    "manufacturer": "Other brand",
                }
            )
        )

    def test_previous_wildcard_would_fail_actual_advertisement(self):
        advertisement = {
            "st": "urn:samsung.com:device:RemoteControlReceiver:1",
            "manufacturer": "Samsung Electronics",
        }
        old_matcher = {"st": advertisement["st"], "manufacturer": "Samsung*"}
        self.assertFalse(advertisement.items() >= old_matcher.items())
        self.assertTrue(self.matches(advertisement))
