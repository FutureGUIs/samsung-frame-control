"""tools/stvp_channels.py layout parsing, on synthetic PDF text positions.

Samsung's PDF itself is not part of the repository; these items mirror its
layout (bold numbers, regular names 28 pt to the right, genre headings).
"""

import importlib.util
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "stvp_channels.py"
spec = importlib.util.spec_from_file_location("stvp_channels", TOOL)
stvp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stvp)

BODY, BOLD = "/X+SamsungSSBody-Regular", "/X+SamsungSSBody-Bold"
HEAD = "/X+SamsungSSHead-Bold"


def number(page, x, y, text):
    return {"page": page, "x": x, "y": y, "font": BOLD, "size": 6.5, "text": text}


def name(page, x, y, text):
    return {"page": page, "x": x + 28, "y": y, "font": BODY, "size": 6.5, "text": text}


def heading(page, x, y, text):
    return {"page": page, "x": x + 28, "y": y, "font": HEAD, "size": 7.5, "text": text}


def channel(page, x, y, num, text):
    return [number(page, x, y, num), name(page, x, y, text)]


def layout():
    return [
        {
            "page": 0,
            "x": 300,
            "y": 580,
            "font": HEAD,
            "size": 20.0,
            "text": "U.S. Channel Lineup",
        },
        {
            "page": 0,
            "x": 330,
            "y": 566,
            "font": "/X+SamsungSSHead-Medium",
            "size": 10.0,
            "text": "Effective: September 2026",
        },
        # Column 1
        heading(0, 24, 524, "News & Opinion"),
        *channel(0, 24, 507, "1110", "CBS News"),
        *channel(0, 24, 497, "1112", "ABC News Live"),
        # Column 2: a two-line name, and a heading mid-column.
        *channel(0, 149, 531, "1226", "Lifetime Movies"),
        name(0, 149, 524, "Love & Drama"),
        *channel(0, 149, 511, "1230", "Movie Hub"),
        heading(0, 149, 490, "Western & Classic TV"),
        *channel(0, 149, 470, "1405", "CINEVAULT: Westerns"),
        # Page 2 continues the last genre before its first heading.
        *channel(1, 24, 531, "1410", "Grit Xtra"),
        heading(1, 24, 515, "Kids"),
        *channel(1, 24, 500, "3610", "Toons"),
        {
            "page": 1,
            "x": 24,
            "y": 20,
            "font": BODY,
            "size": 6.5,
            "text": "Disclaimer: Available for free on select Samsung devices.",
        },
    ]


def test_layout_is_read_column_by_column_with_genres():
    title, effective, channels, leftovers, joined = stvp.parse(layout())
    assert title == "U.S. Channel Lineup"
    assert effective == "Effective: September 2026"
    assert channels == {
        "1110": {"name": "CBS News", "genre": "News & Opinion"},
        "1112": {"name": "ABC News Live", "genre": "News & Opinion"},
        "1226": {"name": "Lifetime Movies Love & Drama", "genre": "News & Opinion"},
        "1230": {"name": "Movie Hub", "genre": "News & Opinion"},
        "1405": {"name": "CINEVAULT: Westerns", "genre": "Western & Classic TV"},
        "1410": {"name": "Grit Xtra", "genre": "Western & Classic TV"},
        "3610": {"name": "Toons", "genre": "Kids"},
    }
    assert joined == ["1226 Lifetime Movies Love & Drama"]
    assert leftovers == []


def test_unplaceable_text_and_numbers_are_reported():
    items = layout() + [
        number(0, 300, 400, "9999"),  # no name beside it
        {"page": 0, "x": 600, "y": 100, "font": BODY, "size": 6.5, "text": "Stray"},
    ]
    _, _, channels, leftovers, _ = stvp.parse(items)
    assert "9999" not in channels
    assert any("9999" in line for line in leftovers)
    assert any("Stray" in line for line in leftovers)


def test_channel_before_any_heading_is_reported():
    _, _, channels, leftovers, _ = stvp.parse(channel(0, 24, 500, "1000", "First"))
    assert channels == {"1000": {"name": "First", "genre": None}}
    assert leftovers == ["channel 1000 has no genre heading"]
