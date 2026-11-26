#!/usr/bin/env python3
"""Automatically sync Recent Writing in README.md from mizore.blog English RSS feed."""

from __future__ import annotations

import email.utils
import pathlib
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
README_PATH = ROOT / "README.md"

FEED_EN_URL = "https://mizore.blog/en/feed.xml"
FEED_ZH_URL = "https://mizore.blog/feed.xml"
MAX_POSTS = 6


def http_get(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "mizorewww-profile-sync/1.0 (+https://github.com/mizorewww)"
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def extract_slug(url: str) -> str:
    match = re.search(r"/posts/([^/?#]+)/?", url)
    return match.group(1) if match else ""


def parse_rss_items(xml_bytes: bytes) -> list[dict[str, str]]:
    root = ET.fromstring(xml_bytes)
    items: list[dict[str, str]] = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_raw = (item.findtext("pubDate") or "").strip()
        if not title or not link:
            continue
        date_str = ""
        if pub_raw:
            dt = email.utils.parsedate_to_datetime(pub_raw)
            date_str = dt.strftime("%Y-%m-%d")
        items.append(
            {
                "title": title,
                "link": link,
                "date": date_str,
                "slug": extract_slug(link),
            }
        )
    return items


def build_writing_markdown(
    en_items: list[dict[str, str]], zh_by_slug: dict[str, str]
) -> str:
    lines: list[str] = []
    for post in en_items[:MAX_POSTS]:
        date_prefix = f"<samp>{post['date']}</samp> — " if post["date"] else ""
        zh_link = zh_by_slug.get(post["slug"])
        zh_suffix = f" · [<samp>ZH</samp>]({zh_link})" if zh_link else ""
        lines.append(f"- {date_prefix}[{post['title']}]({post['link']}){zh_suffix}")
    lines.append("")
    lines.append(
        '<samp><a href="https://mizore.blog/en/">All posts →</a> · '
        '<a href="https://mizore.blog">中文</a> · '
        '<a href="https://mizore.blog/en/feed.xml">RSS</a></samp>'
    )
    return "\n".join(lines)


def replace_section(content: str, marker: str, body: str) -> str:
    pattern = re.compile(
        rf"(<!-- {re.escape(marker)}:START -->).*?(<!-- {re.escape(marker)}:END -->)",
        re.DOTALL,
    )
    replacement = f"<!-- {marker}:START -->\n{body}\n<!-- {marker}:END -->"
    new_content, count = pattern.subn(lambda _: replacement, content)
    if count == 0:
        raise RuntimeError(f"Marker {marker} not found in README.md")
    return new_content


def main() -> int:
    readme = README_PATH.read_text(encoding="utf-8")
    try:
        en_items = parse_rss_items(http_get(FEED_EN_URL))
        zh_items = parse_rss_items(http_get(FEED_ZH_URL))
    except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
        print(f"Failed to fetch RSS feeds: {exc}", file=sys.stderr)
        return 1

    if not en_items:
        print("No English RSS items found; skipping update.", file=sys.stderr)
        return 0

    zh_by_slug = {item["slug"]: item["link"] for item in zh_items if item["slug"]}
    updated = replace_section(
        readme, "WRITING", build_writing_markdown(en_items, zh_by_slug)
    )
    README_PATH.write_text(updated, encoding="utf-8")
    print(f"Synced {min(len(en_items), MAX_POSTS)} English posts from mizore.blog.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
