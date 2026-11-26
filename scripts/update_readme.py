#!/usr/bin/env python3
"""Automatically sync Top 3 External Contributions (with commit counts) and Recent Writing."""

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
ASSETS_DIR = ROOT / "assets"

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

COMMIT_OCTICON_PATH = (
    "M10.5 7.75a2.5 2.5 0 11-5 0 2.5 2.5 0 015 0zm1.43.75a4.002 4.002 0 01-7.86 0H.75"
    "a.75.75 0 110-1.5h3.32a4.001 4.001 0 017.86 0h3.32a.75.75 0 110 1.5h-3.32z"
)


def http_get(url: str, headers: dict[str, str] | None = None) -> bytes:
    req_headers = {
        "User-Agent": "mizorewww-profile-sync/1.0 (+https://github.com/mizorewww)",
        **(headers or {}),
    }
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read()


def github_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_sponsors(username: str) -> list[dict[str, str]]:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        return []
    query = json.dumps(
        {
            "query": """
            query($login: String!) {
              user(login: $login) {
                sponsors(first: 100) {
                  nodes {
                    ... on User { login name avatarUrl }
                    ... on Organization { login name avatarUrl }
                  }
                }
              }
            }
            """,
            "variables": {"login": username},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=query,
        headers={
            "User-Agent": "mizorewww-profile-sync/1.0 (+https://github.com/mizorewww)",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    nodes = (((payload.get("data") or {}).get("user") or {}).get("sponsors") or {}).get("nodes") or []
    return [n for n in nodes if n and n.get("login")]


def build_sponsors_markdown(sponsors: list[dict[str, str]]) -> str:
    if not sponsors:
        return ""
    avatars = " ".join(
        f'<a href="https://github.com/{s["login"]}" title="{s.get("name") or s["login"]}">'
        f'<img src="{s["avatarUrl"]}&s=72" width="36" height="36" alt="@{s["login"]}" />'
        f"</a>"
        for s in sponsors
    )
    return f"\n#### Sponsors\n\n<p align=\"left\">\n  {avatars}\n</p>\n"


def fetch_commit_count(username: str, full_name: str, fallback_prs: int) -> int:
    headers = github_headers()
    query = urllib.parse.quote(f"author:{username} repo:{full_name}")
    url = f"https://api.github.com/search/commits?q={query}&per_page=1"
    try:
        data = json.loads(http_get(url, headers=headers).decode("utf-8"))
        count = int(data.get("total_count", 0))
        if count > 0:
            return count
    except (urllib.error.URLError, TimeoutError, ValueError):
        pass

    try:
        rest_url = f"https://api.github.com/repos/{full_name}/commits?author={username}&per_page=100"
        items = json.loads(http_get(rest_url, headers=headers).decode("utf-8"))
        if isinstance(items, list) and len(items) > 0:
            return len(items)
    except (urllib.error.URLError, TimeoutError, ValueError):
        pass

    return max(fallback_prs, 1)


def fetch_top_contributions(username: str, limit: int) -> list[dict]:
    headers = github_headers()
    merged_pr_counts: dict[str, int] = {}

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
                merged_pr_counts[repo_url] = merged_pr_counts.get(repo_url, 0) + 1
        if len(items) < 100:
            break

    repos: list[dict] = []
    for repo_url, pr_count in sorted(merged_pr_counts.items()):
        try:
            repo = json.loads(http_get(repo_url, headers=headers).decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError):
            continue
        if repo.get("private") or repo.get("fork") or repo.get("archived"):
            continue
        repo["_merged_prs"] = pr_count
        repos.append(repo)

    repos.sort(key=lambda r: int(r.get("stargazers_count", 0)), reverse=True)
    top = repos[:limit]
    for repo in top:
        repo["_commits"] = fetch_commit_count(
            username, repo["full_name"], repo["_merged_prs"]
        )
    return top


def inject_commits_into_pin_svg(svg_text: str, commit_count: int) -> str:
    label = f"{commit_count:,} commit" if commit_count == 1 else f"{commit_count:,} commits"

    # Locate the bottom metadata row `<g transform="translate(30, ...)">`
    row_pattern = re.compile(
        r'(<g transform="translate\(30,\s*\d+\)">)(.*?)(\n\s*</g>\s*<svg x="0" y="0">)',
        re.DOTALL,
    )
    match = row_pattern.search(svg_text)
    if not match:
        return svg_text

    row_body = match.group(2)
    # Find all top-level item transforms and text labels in the bottom row
    item_matches = list(
        re.finditer(
            r'<g transform="translate\(([0-9.]+),\s*0\)">.*?<text[^>]*>([^<]+)</text>',
            row_body,
            re.DOTALL,
        )
    )
    if item_matches:
        last_x = float(item_matches[-1].group(1))
        last_label = item_matches[-1].group(2).strip()
        # Primary lang uses x=15 offset without 20px icon group; star/fork use 20px icon group
        is_lang_only = "primary-lang" in item_matches[-1].group(0) and len(item_matches) == 1
        icon_pad = 15.0 if is_lang_only else 20.0
        next_x = last_x + icon_pad + len(last_label) * 7.0 + 18.0
    else:
        next_x = 0.0

    commit_group = (
        f'<g transform="translate({next_x:.1f}, 0)">'
        f'<g transform="translate(0, 0)">'
        f'<svg class="icon" y="-12" viewBox="0 0 16 16" version="1.1" width="16" height="16">'
        f'<path fill-rule="evenodd" d="{COMMIT_OCTICON_PATH}"/>'
        f"</svg>"
        f"</g>"
        f'<g transform="translate(20, 0)">'
        f'<text data-testid="commitcount" class="gray">{label}</text>'
        f"</g>"
        f"</g>"
    )

    new_row = f"{match.group(1)}{row_body}{commit_group}{match.group(3)}"
    return svg_text[: match.start()] + new_row + svg_text[match.end() :]


def generate_contribution_cards(top_repos: list[dict]) -> str:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    # Remove stale contrib SVGs if count ever shrinks
    for old_svg in ASSETS_DIR.glob("contrib-*.svg"):
        if not any(
            old_svg.name.startswith(f"contrib-{idx}-")
            for idx in range(1, len(top_repos) + 1)
        ):
            old_svg.unlink()

    rows: list[str] = []
    for idx, repo in enumerate(top_repos, start=1):
        full_name = repo["full_name"]
        commits = int(repo["_commits"])
        encoded_repo = urllib.parse.quote(full_name, safe="/")
        base = (
            f"https://github-stats-extended.vercel.app/api/pin/"
            f"?username={GITHUB_USER}&repo={encoded_repo}"
            f"&show_owner=true&card_width=738&description_lines_count=1"
        )

        for mode, style in (("light", LIGHT_STYLE), ("dark", DARK_STYLE)):
            raw_svg = http_get(f"{base}&{style}").decode("utf-8")
            patched_svg = inject_commits_into_pin_svg(raw_svg, commits)
            (ASSETS_DIR / f"contrib-{idx}-{mode}.svg").write_text(
                patched_svg, encoding="utf-8"
            )

        repo_url = f"https://github.com/{full_name}"
        rows.append(
            f'<p align="left">\n'
            f'  <a href="{repo_url}">'
            f"<picture>"
            f'<source media="(prefers-color-scheme: dark)" srcset="./assets/contrib-{idx}-dark.svg" />'
            f'<source media="(prefers-color-scheme: light)" srcset="./assets/contrib-{idx}-light.svg" />'
            f'<img align="center" alt="{full_name} ({commits} commits)" src="./assets/contrib-{idx}-light.svg" />'
            f"</picture>"
            f"</a>\n"
            f"</p>"
        )
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
        top_contribs = fetch_top_contributions(GITHUB_USER, TOP_CONTRIBS_COUNT)
        if top_contribs:
            readme = replace_section(
                readme, "CONTRIBUTIONS", generate_contribution_cards(top_contribs)
            )
            summary = ", ".join(
                f"{r['full_name']} ({r['stargazers_count']} ★, {r['_commits']} commits)"
                for r in top_contribs
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

    try:
        sponsors = fetch_sponsors(GITHUB_USER)
        readme = replace_section(
            readme, "SPONSORS", build_sponsors_markdown(sponsors)
        )
        print(f"Synced {len(sponsors)} GitHub sponsors.")
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        print(
            f"Warning: failed to fetch sponsors ({exc}); keeping existing section.",
            file=sys.stderr,
        )

    README_PATH.write_text(readme, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
