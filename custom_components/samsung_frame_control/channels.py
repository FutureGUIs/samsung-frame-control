"""Samsung TV Plus channel names: built-in U.S. list and Samsung's web API.

channels_us.json ships with the integration (tools/stvp_channels.py builds it
from Samsung's published U.S. PDF). If the user opts in, the line-up for the
chosen country is downloaded from the API behind Samsung's own Samsung TV Plus
page (number, name, genre, logo), at setup, on request, and when the country
changes. Controlling the TV never depends on it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import aiohttp

CHANNELS_FILE = Path(__file__).with_name("channels_us.json")


def load_channels(path: Path = CHANNELS_FILE) -> dict:
    """{"lineup", "effective", "channels": {number: {"name", "genre"}}}."""
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {"lineup": None, "effective": None, "channels": {}}
    raw = data.get("channels") if isinstance(data, dict) else None
    channels = {}
    for number, info in (raw or {}).items():
        if not str(number).isdigit() or not isinstance(info, dict):
            continue
        name = str(info.get("name") or "").strip()
        if name:
            channels[str(number)] = {"name": name, "genre": info.get("genre")}
    return {
        "lineup": data.get("lineup"),
        "effective": data.get("effective"),
        "channels": channels,
    }


OTHER_GENRE = "Other channels"
API = "https://vdapi.samsung.com/tvs/tvpersonalize/api/tvapps/appserver/"
REQUEST_TIMEOUT = 20
CONCURRENCY = 5  # genre requests in flight (the U.S. has 20 genres)
MIN_CHANNELS = 10


class ChannelListError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


async def _get(session, path):
    try:
        async with session.get(
            API + path, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        ) as response:
            if response.status != 200:
                raise ChannelListError(f"http_{response.status}")
            return await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, OSError):
        raise ChannelListError("cannot_connect") from None
    except ValueError:
        raise ChannelListError("invalid_response") from None


async def fetch_lineup(session, country: str) -> dict:
    """Download one country's line-up; raises ChannelListError."""
    data = await _get(session, f"tvplusGenre?country={country}")
    genres = data.get("tvPlusGenre") if isinstance(data, dict) else None
    names = [
        str(g["GENRENAME"])
        for g in genres or []
        if isinstance(g, dict) and g.get("GENRENAME")
    ]
    if not names:
        raise ChannelListError("no_lineup")
    try:
        order = await _get(session, f"tvplusGenreOrder?country={country}")
        ranked = sorted(
            (int(item["GENREORDER"]), str(item["GENRENAME"]))
            for item in order
            if isinstance(item, dict) and str(item.get("GENREORDER", "")).isdigit()
        )
        ordered = [name for _, name in ranked if name in names]
        names = ordered + [name for name in names if name not in ordered]
    except (ChannelListError, KeyError, TypeError):
        pass  # the order is cosmetic
    limit = asyncio.Semaphore(CONCURRENCY)

    async def genre_apps(genre):
        async with limit:
            return await _get(
                session,
                f"tvplusApp?country={country}&genre={quote(genre)}"
                "&order=asc&offset=0&size=999",
            )

    results = await asyncio.gather(*(genre_apps(name) for name in names))
    channels = {}
    for genre, apps in zip(names, results, strict=True):
        for app in apps if isinstance(apps, list) else []:
            if not isinstance(app, dict) or app.get("HIDE") == "Y":
                continue
            number = str(app.get("CHANNELNUMBER") or "").strip()
            name = str(app.get("CHANNELNAME") or "").strip()
            if not number.isdigit() or not name:
                continue
            logo = str(app.get("LOGOURL") or "")
            channels.setdefault(
                number,
                {
                    "name": name,
                    "genre": str(app.get("GENRENAME") or genre),
                    "logo": logo if logo.startswith("https://") else None,
                },
            )
    if len(channels) < MIN_CHANNELS:
        raise ChannelListError("too_few_channels")
    return {
        "region": country,
        "fetched": datetime.now(UTC).isoformat(timespec="seconds"),
        "genre_order": names,
        "channels": dict(sorted(channels.items(), key=lambda kv: int(kv[0]))),
    }


def downloaded(controller) -> dict | None:
    """The downloaded line-up, when enabled and for the chosen country."""
    lineup = controller.channel_lineup
    if (
        controller.channel_download
        and lineup
        and lineup.get("region") == controller.channel_region
    ):
        return lineup
    return None


def base_channels(controller) -> dict[str, dict]:
    """Downloaded line-up for the chosen country, else the built-in U.S. one."""
    if lineup := downloaded(controller):
        return lineup.get("channels") or {}
    if controller.channel_region == "US":
        return controller.channel_list["channels"]
    return {}


def genre_order(controller) -> list[str]:
    lineup = downloaded(controller)
    return list(lineup.get("genre_order") or []) if lineup else []


def channel_names(controller, user_names) -> dict[str, dict]:
    """Every named channel: base line-up, with the user's names first."""
    channels = {
        number: dict(info) for number, info in base_channels(controller).items()
    }
    for number, name in (user_names or {}).items():
        entry = channels.setdefault(str(number), {"genre": OTHER_GENRE})
        entry["name"] = name
    return dict(sorted(channels.items(), key=lambda kv: (len(kv[0]), kv[0])))


def channel_label(number, channels) -> str:
    name = channels.get(number, {}).get("name")
    return f"{number} · {name}" if name else number
