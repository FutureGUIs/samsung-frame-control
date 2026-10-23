#!/usr/bin/env python3
"""Build the Samsung TV Plus channel list the integration ships with.

Reads Samsung's published U.S. line-up PDF and writes a JSON file of channel
number -> name and genre. The integration only reads that file; it never goes
online for channel names. Run this when Samsung publishes a new line-up:

  pip install pypdf
  python tools/stvp_channels.py                          # download from Samsung
  python tools/stvp_channels.py --pdf STVP_Channel-List.pdf

Writes custom_components/samsung_frame_control/channels_us.json by default and
prints a summary plus anything it could not place, so a layout change in a new
PDF is noticed instead of silently producing a wrong list.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

SOURCE_URL = "https://www.samsungtvplus.com/assets/STVP_Channel-List.pdf"
OUT = (
    Path(__file__).resolve().parents[1]
    / "custom_components/samsung_frame_control/channels_us.json"
)
NUMBER = re.compile(r"^\d{3,5}$")
# A name sits about 28 pt right of its number on the same baseline.
NAME_OFFSET = (15.0, 45.0)
# Wrapped names continue on the next line, about 6-8 pt lower.
WRAP_GAP = 9.0


def read_items(pdf_bytes):
    """Every text run as (page, x, y, font, size, text)."""
    try:
        import pypdf
    except ImportError:
        sys.exit("This tool needs pypdf: pip install pypdf")
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    items = []
    for page_number, page in enumerate(reader.pages):

        def visit(text, cm, tm, font, size, page_number=page_number):
            text = text.strip()
            if not text:
                return
            matrix = pypdf.mult(tm, cm)
            items.append(
                {
                    "page": page_number,
                    "x": matrix[4],
                    "y": matrix[5],
                    "font": str((font or {}).get("/BaseFont", "")),
                    "size": round(size * matrix[0], 1),
                    "text": " ".join(text.split()),
                }
            )

        page.extract_text(visitor_text=visit)
    return items


def kind(item):
    font, size = item["font"], item["size"]
    if "Head" in font and size >= 15:
        return "title"
    if "Head" in font and size >= 9:
        return "subtitle"
    if "Head" in font:
        return "genre"
    if "Bold" in font and NUMBER.match(item["text"]):
        return "number"
    return "text"


def parse(items):
    title = next((i["text"] for i in items if kind(i) == "title"), "")
    effective = next((i["text"] for i in items if kind(i) == "subtitle"), "")
    channels, leftovers, joined = {}, [], []
    # A genre continues onto the next page until a new heading appears.
    current = None
    for page in sorted({i["page"] for i in items}):
        on_page = [i for i in items if i["page"] == page]
        numbers = [i for i in on_page if kind(i) == "number"]
        texts = [
            i
            for i in on_page
            if kind(i) == "text" and not i["text"].startswith("Disclaimer")
        ]
        genres = [i for i in on_page if kind(i) == "genre"]
        # Columns are the distinct x positions of channel numbers.
        columns = []
        for x in sorted(i["x"] for i in numbers):
            if not columns or x - columns[-1] > 20:
                columns.append(x)

        def column_of(x):
            index = 0
            for n, start in enumerate(columns):
                if x >= start - 5:
                    index = n
            return index

        used = set()
        rows = []
        for number in numbers:
            name = next(
                (
                    t
                    for t in texts
                    if id(t) not in used
                    and abs(t["y"] - number["y"]) < 1.0
                    and NAME_OFFSET[0] <= t["x"] - number["x"] <= NAME_OFFSET[1]
                ),
                None,
            )
            if name is None:
                leftovers.append(
                    f"page {page + 1}: number {number['text']} has no name"
                )
                continue
            used.add(id(name))
            rows.append(
                {
                    "kind": "channel",
                    "column": column_of(number["x"]),
                    "y": number["y"],
                    "number": number["text"],
                    "name": name["text"],
                    "name_x": name["x"],
                }
            )
        for genre in genres:
            rows.append(
                {
                    "kind": "genre",
                    "column": column_of(genre["x"]),
                    "y": genre["y"],
                    "name": genre["text"],
                }
            )
        # Reading order: column by column, top to bottom.
        rows.sort(key=lambda r: (r["column"], -r["y"]))
        # Wrapped name lines: same column and x as a name, just below it.
        for text in texts:
            if id(text) in used:
                continue
            above = [
                r
                for r in rows
                if r["kind"] == "channel"
                and abs(r["name_x"] - text["x"]) < 1.0
                and 0 < r["y"] - text["y"] <= WRAP_GAP
            ]
            if above:
                row = min(above, key=lambda r: r["y"] - text["y"])
                row["name"] += " " + text["text"]
                used.add(id(text))
                joined.append(f"{row['number']} {row['name']}")
            else:
                leftovers.append(f"page {page + 1}: unplaced text {text['text']!r}")
        for row in rows:
            if row["kind"] == "genre":
                current = row["name"]
                continue
            if row["number"] in channels:
                leftovers.append(f"duplicate channel {row['number']}")
            channels[row["number"]] = {"name": row["name"], "genre": current}
            if current is None:
                leftovers.append(f"channel {row['number']} has no genre heading")
    return title, effective, channels, leftovers, joined


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pdf", type=Path, help="local PDF instead of downloading")
    parser.add_argument("--url", default=SOURCE_URL)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    if args.pdf:
        data = args.pdf.read_bytes()
    else:
        print(f"Downloading {args.url} ...")
        request = urllib.request.Request(
            args.url, headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
    title, effective, channels, leftovers, joined = parse(read_items(data))
    if not channels:
        sys.exit("No channels found: the PDF layout may have changed.")
    ordered = dict(sorted(channels.items(), key=lambda kv: int(kv[0])))
    output = {
        "lineup": title,
        "effective": effective.removeprefix("Effective:").strip(),
        "source": args.url if not args.pdf else SOURCE_URL,
        "generated": date.today().isoformat(),
        "channels": ordered,
    }
    args.out.write_text(
        json.dumps(output, ensure_ascii=False, indent=1) + "\n", "utf-8"
    )
    genres = {}
    for channel in ordered.values():
        genres[channel["genre"]] = genres.get(channel["genre"], 0) + 1
    print(f"{title}, effective {output['effective']}: {len(ordered)} channels")
    for genre, count in genres.items():
        print(f"  {count:4d}  {genre}")
    for line in joined:
        print("joined two-line name:", line)
    for line in leftovers:
        print("WARNING:", line)
    print(f"Wrote {args.out}")
    return 1 if leftovers else 0


if __name__ == "__main__":
    sys.exit(main())
