"""Write one Tableau dashboard extension manifest (.trex) per agent listed in ../.env.

Each manifest points Tableau at the chat app with that agent preselected, e.g.
http://localhost:8501/?agent=PHARMA&mode=extension

Usage: python build_trex.py [--base-url http://localhost:8501] [--suffix hosted]
"""
import argparse
import base64
import os
import struct
import zlib
from pathlib import Path
from xml.sax.saxutils import escape

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
AUTHOR_EMAIL = "rrkcts2007@gmail.com"


def icon_png(size=70):
    """70x70 PNG (the size Tableau expects): white chat bubble on navy."""
    navy, white = (0x00, 0x28, 0x55), (0xFF, 0xFF, 0xFF)
    rows = []
    for y in range(size):
        row = bytearray([0])  # PNG filter byte
        for x in range(size):
            in_bubble = 14 <= x <= 56 and 16 <= y <= 44
            in_tail = 20 <= x <= 30 and 44 < y <= 54 and (x - 20) <= (54 - y)
            on_line = in_bubble and y in (25, 26, 34, 35) and 21 <= x <= (49 if y < 30 else 41)
            row += bytes(navy if on_line else white if (in_bubble or in_tail) else navy)
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def trex(agent_id, label, base_url, icon_b64, suffix=""):
    url = f"{base_url.rstrip('/')}/?agent={agent_id}&mode=extension"
    # Tableau only accepts an https:// author website (the source URL may be http://localhost).
    website = base_url if base_url.lower().startswith("https://") else "https://localhost"
    # A suffix gives the manifest its own extension id and name, so Tableau can hold both variants.
    ext_id = agent_id.lower() + (f".{suffix}" if suffix else "")
    if suffix:
        label = f"{label} ({suffix.title()})"
    return f"""<?xml version="1.0" encoding="utf-8"?>
<manifest manifest-version="0.1" xmlns="http://www.tableau.com/xml/extension_manifest">
  <dashboard-extension id="com.tableaucortexdemo.chat.{ext_id}" extension-version="1.0.0">
    <default-locale>en_US</default-locale>
    <name resource-id="name"/>
    <description>Chat with the Snowflake Cortex {escape(label)} agent. Uses the dashboard's parameters and filter selections as context.</description>
    <author name="Tableau Cortex Agent Demo" email="{AUTHOR_EMAIL}" organization="Tableau Cortex Agent Demo" website="{escape(website)}"/>
    <min-api-version>1.4</min-api-version>
    <source-location>
      <url>{escape(url)}</url>
    </source-location>
    <icon>{icon_b64}</icon>
  </dashboard-extension>
  <resources>
    <resource id="name">
      <text locale="en_US">Cortex Chat - {escape(label)}</text>
    </resource>
  </resources>
</manifest>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8501")
    ap.add_argument("--suffix", default="", help="e.g. hosted -> cortex_chat_pharma_hosted.trex")
    args = ap.parse_args()
    load_dotenv(HERE.parent / ".env")
    icon_b64 = base64.b64encode(icon_png()).decode()
    ids = [i.strip().upper() for i in os.getenv("AGENTS", "").split(",") if i.strip()] or ["DEFAULT"]
    for agent_id in ids:
        label = os.getenv(f"{agent_id}_LABEL", os.getenv("AGENT_LABEL", agent_id.title()))
        name = agent_id.lower() + (f"_{args.suffix.lower()}" if args.suffix else "")
        out = HERE / f"cortex_chat_{name}.trex"
        out.write_text(trex(agent_id, label, args.base_url, icon_b64, args.suffix.lower()), encoding="utf-8")
        print(f"wrote {out.name} -> {args.base_url}/?agent={agent_id}&mode=extension")


if __name__ == "__main__":
    main()
