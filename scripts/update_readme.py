#!/usr/bin/env python3
"""Automatically sync Top 3 External Contributions and Recent Writing in README.md."""

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
TOP_CONTRIBS_COUNT = 3

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


def github_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_top_contributions(username: str, limit: int) -> list[dict]:
    headers = github_headers()
    repo_urls: set[str] = set()

    for page in range(1, 6):
        query = urllib.parse.quote(
            f"author:{username} type:pr is:merged -user:{username} is:public"
        )
        search_url = (
            f"https://api.github.com/search/issues?q={query}&per_page=100&page={page}"
        )
        data = json.loads(http_get(search_url, headers=headers).decode("utf-8"))
        items = data.get("items") or []
        for item in items:
            repo_url = item.get("repository_url")
            if repo_url:
                repo_urls.add(repo_url)
        if len(items) < 100:
            break

    repos: list[dict] = []
    for repo_url in sorted(repo_urls):
        try:
            repo = json.loads(http_get(repo_url, headers=headers).decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError):
            continue
        if repo.get("private") or repo.get("fork") or repo.get("archived"):
            continue
        repos.append(repo)

    repos.sort(key=lambda r: int(r.get("stargazers_count", 0)), reverse=True)
    return repos[:limit]


def build_contrib_card_html(full_name: str) -> str:
    encoded_repo = urllib.parse.quote(full_name, safe="/")
    base = (
        f"https://github-stats-extended.vercel.app/api/pin/"
        f"?username={GITHUB_USER}&repo={encoded_repo}"
        f"&show_owner=true&card_width=738&description_lines_count=1"
    )
    light_url = f"{base}&{LIGHT_STYLE}"
    dark_url = f"{base}&{DARK_STYLE}"
    repo_url = f"https://github.com/{full_name}"
    return (
        f'<p align="left">\n'
        f'  <a href="{repo_url}">'
        f"<picture>"
        f'<source media="(prefers-color-scheme: dark)" srcset="{dark_url}" />'
        f'<source media="(prefers-color-scheme: light)" srcset="{light_url}" />'
        f'<img align="center" alt="{full_name}" src="{light_url}" />'
        f"</picture>"
        f"</a>\n"
        f"</p>"
    )


def build_contributions_markdown(top_repos: list[dict]) -> str:
    return "\n".join(build_contrib_card_html(r["full_name"]) for r in top_repos)


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
        top_contribs = fetch_top_contributions(GITHUB_USER, TOP_CONTRIBS_COUNT)
        if top_contribs:
            readme = replace_section(
                readme, "CONTRIBUTIONS", build_contributions_markdown(top_contribs)
            )
            summary = ", ".join(
                f"{r['full_name']} ({r['stargazers_count']})" for r in top_contribs
            )
            print(f"Synced top {len(top_contribs)} contributions: {summary}")
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        print(
            f"Warning: failed to fetch top contributions ({exc}); keeping existing section.",
            file=sys.stderr,
        )

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
        print(
            f"Warning: failed to fetch RSS feeds ({exc}); keeping existing section.",
            file=sys.stderr,
        )

    README_PATH.write_text(readme, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
