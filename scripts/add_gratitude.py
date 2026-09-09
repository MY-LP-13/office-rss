#!/usr/bin/env python3
"""Add one approved submission to gratitude-feed.xml.

Reads the submission from environment variables so that nothing from the
form is ever parsed as shell. Rewrites the whole feed from a validated
model, so the file cannot drift into invalid XML.
"""

import hashlib
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from xml.sax.saxutils import escape

FEED = "gratitude-feed.xml"
MAX_ITEMS = 40
LIMITS = {"name": 60, "grateful_for": 90, "message": 300}

CHANNEL_TITLE = "LeavePlus Gratitude Board"
CHANNEL_DESC = "Gratitude shared by the LeavePlus team"

try:
    from zoneinfo import ZoneInfo
    MELBOURNE = ZoneInfo("Australia/Melbourne")
except Exception:                                    # pragma: no cover
    MELBOURNE = timezone(timedelta(hours=10))


def clean(raw, limit):
    """Collapse whitespace, drop control characters, trim on a word boundary."""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", raw or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return (cut or text[:limit]) + "…"


def sort_key(raw):
    """Order key for existing items; unreadable dates sort to the bottom."""
    try:
        parsed = parsedate_to_datetime((raw or "").strip())
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MELBOURNE)
    return parsed.astimezone(timezone.utc)


def read_date(raw):
    """Accept ISO-8601 (what the flow sends) or RFC-822, and return it as
    Melbourne local time. Anything unreadable becomes 'now'.

    The flow sends plain UTC and this owns the conversion, so daylight
    saving is handled here rather than hard-coded to +1000 in Power Automate.
    """
    raw = (raw or "").strip()
    if raw:
        parsed = None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(MELBOURNE)
    return datetime.now(MELBOURNE)


def load_items():
    """Return existing items, or an empty list if the feed is unreadable."""
    if not os.path.exists(FEED):
        return []
    with open(FEED, encoding="utf-8") as handle:
        body = handle.read()
    try:
        channel = ET.fromstring(body).find("channel")
    except ET.ParseError as err:
        print(f"::warning::{FEED} was not valid XML ({err}). Rebuilding it.")
        print("::group::Unreadable feed contents (recover any lost entries here)")
        print(body)
        print("::endgroup::")
        return []
    items = []
    for node in (channel.findall("item") if channel is not None else []):
        items.append({
            "title": (node.findtext("title") or "").strip(),
            "category": (node.findtext("category") or "").strip(),
            "description": (node.findtext("description") or "").strip(),
            "pubDate": (node.findtext("pubDate") or "").strip(),
        })
    return items


def render(items, built):
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "  <channel>",
        f"    <title>{escape(CHANNEL_TITLE)}</title>",
        f"    <description>{escape(CHANNEL_DESC)}</description>",
        f"    <lastBuildDate>{format_datetime(built)}</lastBuildDate>",
    ]
    for item in items:
        seed = f"{item['title']}|{item['description']}|{item['pubDate']}"
        guid = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
        lines.append("    <item>")
        lines.append(f"      <title>{escape(item['title'])}</title>")
        if item["category"]:
            lines.append(f"      <category>{escape(item['category'])}</category>")
        lines.append(f"      <description>{escape(item['description'])}</description>")
        lines.append(f"      <pubDate>{escape(item['pubDate'])}</pubDate>")
        lines.append(f'      <guid isPermaLink="false">{guid}</guid>')
        lines.append("    </item>")
    lines += ["  </channel>", "</rss>", ""]
    return "\n".join(lines)


def main():
    name = clean(os.environ.get("GB_NAME"), LIMITS["name"]) or "a colleague"
    grateful_for = clean(os.environ.get("GB_GRATEFUL_FOR"), LIMITS["grateful_for"])
    message = clean(os.environ.get("GB_MESSAGE"), LIMITS["message"])

    if not message and not grateful_for:
        print("::error::Submission had no message and no subject. Nothing to add.")
        return 1

    published = read_date(os.environ.get("GB_DATE"))
    entry = {
        "title": name,
        "category": grateful_for,
        "description": message or grateful_for,
        "pubDate": format_datetime(published),
    }

    items = load_items()
    if any(
        i["title"] == entry["title"]
        and i["description"] == entry["description"]
        and i["pubDate"] == entry["pubDate"]
        for i in items
    ):
        print("::notice::Identical entry already in the feed. Skipping.")
        return 0

    items.insert(0, entry)
    # Sort newest first so a back-dated or out-of-order dispatch still lands
    # in the right place. Unparseable dates sink to the bottom rather than
    # blocking the write.
    items.sort(key=lambda i: sort_key(i["pubDate"]), reverse=True)
    dropped = max(0, len(items) - MAX_ITEMS)
    items = items[:MAX_ITEMS]

    output = render(items, datetime.now(MELBOURNE))
    ET.fromstring(output)                      # refuse to write anything invalid

    with open(FEED, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(output)

    print(f"Added entry from {name}. Feed now holds {len(items)} items"
          + (f"; {dropped} oldest dropped." if dropped else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
