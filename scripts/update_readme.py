#!/usr/bin/env python3
"""Automatically sync Top 6 Repositories and Recent Writing in README.md."""

from __future__ import annotations

import email.utils
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
README_PATH = ROOT / "README.md"

GITHUB_USER = "mizorewww"
TOP_REPOS_COUNT = 6

FEED_EN_URL = "https://mizore.blog/en/feed.xml"
FEED_ZH_URL = "https://mizore.blog/feed.xml"
MAX_POSTS = 6

LIGHT_STYLE = (
    "border_radius=12&bg_color=FFF9FB&border_color=F6DCE4"
    "&title_color=D86C8E&text_color=4A4246&icon_color=EC8CA9"
)
DARK_STYLE = (
    "border_radius=12&bg_color=161316&border_color=36272E"
    "&title_color=F4A8C0&text_color=E6DCE0&icon_color=F4A8C0"
)


def http_get(url: str, headers: dict[str, str] | None = None) -> bytes:
    req_headers = {
        "User-Agent": "mizorewww-profile-sync/1.0 (+https://github.com/mizorewww)",
        **(headers or {}),
    }
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def fetch_top_repos(username: str, limit: int) -> list[dict]:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    repos: list[dict] = []
    page = 1
    while True:
        url = f"https://api.github.com/users/{username}/repos?per_page=100&type=owner&page={page}"
        batch = json.loads(http_get(url, headers=headers).decode("utf-8"))
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    owned = [
        r
        for r in repos
        if not r.get("fork")
        and not r.get("archived")
        and r.get("name") != username
    ]
    owned.sort(key=lambda r: int(r.get("stargazers_count", 0)), reverse=True)
    return owned[:limit]


def build_pin_card_html(repo_name: str) -> str:
    encoded_repo = urllib.parse.quote(repo_name, safe="")
    base = (
        f"https://github-stats-extended.vercel.app/api/pin/"
        f"?username={GITHUB_USER}&repo={encoded_repo}"
        f"&card_width=364&description_lines_count=2"
    )
    light_url = f"{base}&{LIGHT_STYLE}"
    dark_url = f"{base}&{DARK_STYLE}"
    repo_url = f"https://github.com/{GITHUB_USER}/{repo_name}"
    return (
        f'<a href="{repo_url}">'
        f"<picture>"
        f'<source media="(prefers-color-scheme: dark)" srcset="{dark_url}" />'
        f'<source media="(prefers-color-scheme: light)" srcset="{light_url}" />'
        f'<img align="center" alt="{repo_name}" src="{light_url}" />'
        f"</picture>"
        f"</a>"
    )


def build_projects_markdown(top_repos: list[dict]) -> str:
    rows: list[str] = []
    for i in range(0, len(top_repos), 2):
        pair = top_repos[i : i + 2]
        cards = "&nbsp;&nbsp;".join(build_pin_card_html(r["name"]) for r in pair)
        rows.append(f'<p align="left">\n  {cards}\n</p>')
    return "\n".join(rows)


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
        top_repos = fetch_top_repos(GITHUB_USER, TOP_REPOS_COUNT)
        if top_repos:
            readme = replace_section(
                readme, "PROJECTS", build_projects_markdown(top_repos)
            )
            names = ", ".join(f"{r['name']} ({r['stargazers_count']})" for r in top_repos)
            print(f"Synced top {len(top_repos)} repos: {names}")
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        print(f"Warning: failed to fetch top repos ({exc}); keeping existing section.", file=sys.stderr)

    try:
        en_items = parse_rss_items(http_get(FEED_EN_URL))
        zh_items = parse_rss_items(http_get(FEED_ZH_URL))
        if en_items:
            zh_by_slug = {item["slug"]: item["link"] for item in zh_items if item["slug"]}
            readme = replace_section(
                readme, "WRITING", build_writing_markdown(en_items, zh_by_slug)
            )
            print(f"Synced {min(len(en_items), MAX_POSTS)} English posts from mizore.blog.")
    except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
        print(f"Warning: failed to fetch RSS feeds ({exc}); keeping existing section.", file=sys.stderr)

    README_PATH.write_text(readme, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
