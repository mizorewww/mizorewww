#!/usr/bin/env python3
"""Sync featured repository star counts, header SVG stats, and recent blog posts."""

from __future__ import annotations

import email.utils
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
README_PATH = ROOT / "README.md"
SVG_PATHS = [
    ROOT / "assets" / "header-light.svg",
    ROOT / "assets" / "header-dark.svg",
]

GITHUB_USER = "mizorewww"
FEED_ZH_URL = "https://mizore.blog/feed.xml"
FEED_EN_URL = "https://mizore.blog/en/feed.xml"
MAX_POSTS = 6

PROJECT_GROUPS: list[tuple[str, list[dict[str, str]]]] = [
    (
        "On-Device AI & Language Models",
        [
            {
                "repo": "laya-mlx",
                "stack": "Python · MLX",
                "desc": "Native Apple Silicon runtime for Laya typed decision models — 7–14 ms calibrated single-pass decisions on M3 Max without PyTorch or cloud APIs.",
            },
            {
                "repo": "laya-coreml",
                "stack": "Python · Core ML",
                "desc": "Validated Core ML and Apple Neural Engine ports for Laya typed decisions (~5 ms short decisions on M3 Max), with reproducible speed and energy benchmarks.",
            },
            {
                "repo": "rime-qwen-freq",
                "stack": "C++ · Python",
                "desc": "Offline unigram word-frequency calibration and PMI `.gram` distillation from open-weight LLMs for the Rime input method engine, with zero runtime latency.",
            },
            {
                "repo": "colab-qwen38-g4",
                "stack": "Python",
                "desc": "One-click 262K-context NVFP4 deployment of Qwen3.8-Flash-Next (with MTP) on Google Colab G4 GPUs.",
            },
            {
                "repo": "colab-account-changer",
                "stack": "Python",
                "desc": "Secure multi-account session switcher for the Google Colab CLI.",
            },
        ],
    ),
    (
        "Desktop Apps & Systems Tooling",
        [
            {
                "repo": "Rayon",
                "stack": "Swift · Ghostty",
                "desc": "Native macOS SSH server manager pairing a GPU-rendered Ghostty terminal, SFTP file browser, and agentless live Linux/macOS/GPU resource monitor.",
            },
            {
                "repo": "x_gift_bot",
                "stack": "Go",
                "desc": "Self-hosted X Premium gift code platform with AES-256-GCM credential encryption, CLI setup wizard, and web redemption console.",
            },
            {
                "repo": "gdirect",
                "stack": "Rust · libaria2",
                "desc": "Direct, resumable Google Drive terminal browser, desktop client, and rclone mount over reachable front-end IPs without proxy bottlenecks.",
            },
            {
                "repo": "course2md",
                "stack": "Rust",
                "desc": "Cross-platform desktop app and CLI turning YouTube, Bilibili, or local lecture videos into slide-illustrated Markdown notes with local speech recognition.",
            },
            {
                "repo": "gpui-kit-material",
                "stack": "Rust · GPUI",
                "desc": "Material 3 Expressive design tokens, motion, and components for GPUI desktop apps, paired with [gpui-material-symbols](https://github.com/mizorewww/gpui-material-symbols).",
            },
        ],
    ),
]


def http_get(url: str, headers: dict[str, str] | None = None) -> bytes:
    req_headers = {
        "User-Agent": "mizorewww-profile-updater/1.0 (+https://github.com/mizorewww)",
        **(headers or {}),
    }
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def fetch_github_repos(username: str) -> list[dict]:
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
    return repos


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


def build_projects_markdown(star_map: dict[str, int]) -> str:
    sections: list[str] = []
    for group_title, items in PROJECT_GROUPS:
        lines = [f"#### {group_title}", ""]
        for item in items:
            repo = item["repo"]
            stars = star_map.get(repo, 0)
            star_badge = f" · <samp>★ {stars:,}</samp>" if stars > 0 else ""
            lines.append(
                f"- **[{repo}](https://github.com/{GITHUB_USER}/{repo})** "
                f"`{item['stack']}`{star_badge}  \n"
                f"  {item['desc']}"
            )
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def build_writing_markdown(
    zh_items: list[dict[str, str]], en_by_slug: dict[str, str]
) -> str:
    lines: list[str] = []
    for post in zh_items[:MAX_POSTS]:
        date_prefix = f"<samp>{post['date']}</samp> — " if post["date"] else ""
        en_link = en_by_slug.get(post["slug"])
        en_suffix = f" · [<samp>EN</samp>]({en_link})" if en_link else ""
        lines.append(f"- {date_prefix}[{post['title']}]({post['link']}){en_suffix}")
    lines.append("")
    lines.append(
        "<samp><a href=\"https://mizore.blog\">All posts →</a> · "
        "<a href=\"https://mizore.blog/en/\">English archive</a> · "
        "<a href=\"https://mizore.blog/feed.xml\">RSS</a></samp>"
    )
    return "\n".join(lines)


def update_svg_stats(total_stars: int, repo_count: int) -> None:
    for svg_path in SVG_PATHS:
        if not svg_path.exists():
            continue
        svg = svg_path.read_text(encoding="utf-8")
        svg = re.sub(
            r'(<tspan id="svg-total-stars"[^>]*>)[^<]*(</tspan>)',
            rf"\g<1>★ {total_stars:,}\g<2>",
            svg,
        )
        svg = re.sub(
            r'(<tspan id="svg-repo-count"[^>]*>)[^<]*(</tspan>)',
            rf"\g<1>{repo_count} repos\g<2>",
            svg,
        )
        svg_path.write_text(svg, encoding="utf-8")


def main() -> int:
    readme = README_PATH.read_text(encoding="utf-8")

    try:
        repos = fetch_github_repos(GITHUB_USER)
        owned = [r for r in repos if not r.get("fork") and r.get("name") != GITHUB_USER]
        star_map = {r["name"]: int(r.get("stargazers_count", 0)) for r in repos}
        total_stars = sum(int(r.get("stargazers_count", 0)) for r in owned)
        readme = replace_section(readme, "PROJECTS", build_projects_markdown(star_map))
        update_svg_stats(total_stars=total_stars, repo_count=len(owned))
        print(f"Updated GitHub stats: {total_stars:,} stars across {len(owned)} original repos.")
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        print(f"Warning: failed to update GitHub stats ({exc}); keeping existing section.", file=sys.stderr)

    try:
        zh_items = parse_rss_items(http_get(FEED_ZH_URL))
        en_items = parse_rss_items(http_get(FEED_EN_URL))
        en_by_slug = {item["slug"]: item["link"] for item in en_items if item["slug"]}
        if zh_items:
            readme = replace_section(
                readme, "WRITING", build_writing_markdown(zh_items, en_by_slug)
            )
            print(f"Updated Recent Writing with {min(len(zh_items), MAX_POSTS)} posts.")
    except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
        print(f"Warning: failed to update RSS posts ({exc}); keeping existing section.", file=sys.stderr)

    README_PATH.write_text(readme, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
