#!/usr/bin/env python3
"""Generate minimalist, light/dark-adaptive GitHub profile SVG cards.

Pure Python 3 standard library — no third-party dependencies required.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

THEMES: dict[str, dict[str, str]] = {
    "light": {
        "bg": "#ffffff",
        "surface": "#f6f8fa",
        "border": "#d1d9e0",
        "divider": "#eaeef2",
        "text": "#1f2328",
        "secondary": "#59636e",
        "muted": "#6e7781",
        "accent": "#0969da",
        "pill_bg": "#f6f8fa",
        "pill_border": "#d1d9e0",
        "pill_text": "#59636e",
        "tag_bg": "#ddf4ff",
        "tag_text": "#0969da",
        "bar_bg": "#eaeef2",
    },
    "dark": {
        "bg": "#0d1117",
        "surface": "#161b22",
        "border": "#30363d",
        "divider": "#21262d",
        "text": "#e6edf3",
        "secondary": "#8d96a0",
        "muted": "#7d8590",
        "accent": "#58a6ff",
        "pill_bg": "#161b22",
        "pill_border": "#30363d",
        "pill_text": "#8d96a0",
        "tag_bg": "#12263f",
        "tag_text": "#58a6ff",
        "bar_bg": "#21262d",
    },
}

FONT_SANS = '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif'
FONT_MONO = 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace'

GRAPHQL_QUERY = """
query($login: String!, $repoOwner: String!, $repoName: String!, $prQuery: String!, $issueQuery: String!) {
  user(login: $login) {
    name
    login
    followers {
      totalCount
    }
    contributionsCollection {
      totalCommitContributions
      restrictedContributionsCount
      commitContributionsByRepository(maxRepositories: 100) {
        repository {
          isPrivate
        }
        contributions {
          totalCount
        }
      }
    }
    repositoriesContributedTo(
      first: 1
      privacy: PUBLIC
      contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, REPOSITORY]
    ) {
      totalCount
    }
    pullRequests(first: 1) {
      totalCount
    }
    openIssues: issues(states: OPEN) {
      totalCount
    }
    closedIssues: issues(states: CLOSED) {
      totalCount
    }
    repositories(
      first: 100
      privacy: PUBLIC
      ownerAffiliations: [OWNER, ORGANIZATION_MEMBER]
      isFork: false
      orderBy: { field: STARGAZERS, direction: DESC }
    ) {
      nodes {
        nameWithOwner
        stargazerCount
        owner {
          login
        }
        languages(first: 10, orderBy: { field: SIZE, direction: DESC }) {
          edges {
            size
            node {
              name
              color
            }
          }
        }
      }
    }
  }
  publicPrs: search(query: $prQuery, type: ISSUE) {
    issueCount
  }
  publicIssues: search(query: $issueQuery, type: ISSUE) {
    issueCount
  }
  featuredRepo: repository(owner: $repoOwner, name: $repoName) {
    nameWithOwner
    name
    owner {
      login
    }
    description
    stargazerCount
    forkCount
    primaryLanguage {
      name
      color
    }
    licenseInfo {
      spdxId
    }
    latestRelease {
      tagName
    }
    repositoryTopics(first: 10) {
      nodes {
        topic {
          name
        }
      }
    }
  }
}
"""


@dataclass
class LanguageStat:
    name: str
    color: str
    size: int
    percent: float


@dataclass
class ProfileStats:
    name: str
    login: str
    stars: int
    commits: int
    prs: int
    issues: int
    contributed_to: int
    followers: int
    languages: list[LanguageStat]


@dataclass
class FeaturedRepo:
    owner: str
    name: str
    description: str
    stars: int
    forks: int
    language_name: str
    language_color: str
    license_id: str | None
    release_tag: str | None
    topics: list[str]


def graphql_request(token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        GITHUB_GRAPHQL_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "cauyxy-profile-card-generator",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub GraphQL HTTP {exc.code}: {body}") from exc

    if data.get("errors"):
        raise RuntimeError(f"GitHub GraphQL errors: {json.dumps(data['errors'], indent=2)}")
    return data["data"]


def format_compact(n: int) -> str:
    if n >= 1_000_000:
        val = f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{val}m"
    if n >= 10_000:
        val = f"{n / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{val}k"
    if n >= 1_000:
        val = f"{n / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{val}k"
    return str(n)


def estimate_text_width(text: str, font_size: float, mono: bool = False) -> float:
    if mono:
        return len(text) * font_size * 0.60
    narrow = set("ilI1.,:;|!'` /-")
    wide = set("MWmw@%&")
    total = 0.0
    for ch in text:
        if ch in narrow:
            total += 0.30
        elif ch in wide:
            total += 0.78
        elif ch.isupper():
            total += 0.62
        else:
            total += 0.50
    return total * font_size


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def fetch_data(
    token: str,
    username: str,
    featured_owner: str,
    featured_repo: str,
    public_only: bool,
) -> tuple[ProfileStats, FeaturedRepo]:
    data = graphql_request(
        token,
        GRAPHQL_QUERY,
        {
            "login": username,
            "repoOwner": featured_owner,
            "repoName": featured_repo,
            "prQuery": f"author:{username} type:pr is:public",
            "issueQuery": f"author:{username} type:issue is:public",
        },
    )

    user = data["user"]
    contrib = user["contributionsCollection"]

    if public_only:
        commits = sum(
            item["contributions"]["totalCount"]
            for item in contrib["commitContributionsByRepository"]
            if not item["repository"]["isPrivate"]
        )
        prs = data["publicPrs"]["issueCount"]
        issues = data["publicIssues"]["issueCount"]
    else:
        commits = contrib["totalCommitContributions"] + contrib["restrictedContributionsCount"]
        prs = user["pullRequests"]["totalCount"]
        issues = user["openIssues"]["totalCount"] + user["closedIssues"]["totalCount"]

    repos = user["repositories"]["nodes"]
    total_stars = sum(r["stargazerCount"] for r in repos)

    # Aggregate language bytes across user-owned public non-fork repos
    lang_bytes: dict[str, int] = {}
    lang_colors: dict[str, str] = {}
    for repo in repos:
        if repo["owner"]["login"].lower() != username.lower():
            continue
        for edge in repo["languages"]["edges"]:
            lang_name = edge["node"]["name"]
            lang_bytes[lang_name] = lang_bytes.get(lang_name, 0) + edge["size"]
            if edge["node"]["color"]:
                lang_colors[lang_name] = edge["node"]["color"]

    sorted_langs = sorted(lang_bytes.items(), key=lambda kv: kv[1], reverse=True)[:8]
    total_bytes = sum(size for _, size in sorted_langs) or 1
    languages = [
        LanguageStat(
            name=name,
            color=lang_colors.get(name, "#8b949e"),
            size=size,
            percent=(size / total_bytes) * 100.0,
        )
        for name, size in sorted_langs
    ]

    stats = ProfileStats(
        name=user.get("name") or username,
        login=user["login"],
        stars=total_stars,
        commits=commits,
        prs=prs,
        issues=issues,
        contributed_to=user["repositoriesContributedTo"]["totalCount"],
        followers=user["followers"]["totalCount"],
        languages=languages,
    )

    repo_data = data["featuredRepo"]
    raw_topics = [n["topic"]["name"] for n in repo_data["repositoryTopics"]["nodes"]]
    # Pick concise, non-redundant topics for clean display
    primary_lang = (repo_data.get("primaryLanguage") or {}).get("name") or ""
    skip_topics = {primary_lang.lower(), "csharp", "bepinex-plugin", "unity-mod", "the-bazaar"}
    curated_topics = [t for t in raw_topics if t.lower() not in skip_topics][:5]

    license_id = (repo_data.get("licenseInfo") or {}).get("spdxId")
    if license_id == "NOASSERTION":
        license_id = None

    release_tag = (repo_data.get("latestRelease") or {}).get("tagName")
    if release_tag and not release_tag.startswith(("v", "V")):
        release_tag = f"v{release_tag}"

    featured = FeaturedRepo(
        owner=repo_data["owner"]["login"],
        name=repo_data["name"],
        description=repo_data.get("description") or "",
        stars=repo_data["stargazerCount"],
        forks=repo_data["forkCount"],
        language_name=primary_lang or "Code",
        language_color=(repo_data.get("primaryLanguage") or {}).get("color") or "#8b949e",
        license_id=license_id,
        release_tag=release_tag,
        topics=curated_topics,
    )

    return stats, featured


def render_overview_svg(stats: ProfileStats, theme_name: str) -> str:
    t = THEMES[theme_name]
    width, height = 840, 214

    # Build segmented language bar (x=452, y=62, width=363, height=8)
    bar_x, bar_y, bar_w, bar_h = 452.0, 62.0, 363.0, 8.0
    gap = 1.5
    n_langs = len(stats.languages)
    usable_w = bar_w - max(0, n_langs - 1) * gap

    raw_widths = [max(3.0, (lang.percent / 100.0) * usable_w) for lang in stats.languages]
    scale = usable_w / sum(raw_widths) if raw_widths else 1.0
    seg_widths = [w * scale for w in raw_widths]

    bar_rects: list[str] = []
    cursor_x = bar_x
    for lang, seg_w in zip(stats.languages, seg_widths):
        bar_rects.append(
            f'<rect x="{cursor_x:.2f}" y="{bar_y:.1f}" width="{seg_w:.2f}" '
            f'height="{bar_h:.1f}" fill="{escape(lang.color)}" />'
        )
        cursor_x += seg_w + gap

    # Build 2-column x 4-row language legend
    lang_items: list[str] = []
    row_ys = [94, 116, 138, 160]
    for idx, lang in enumerate(stats.languages[:8]):
        col = idx // 4
        row = idx % 4
        y = row_ys[row]
        dot_x = 457 if col == 0 else 649
        name_x = 468 if col == 0 else 660
        pct_x = 623 if col == 0 else 815
        display_name = "Jupyter" if lang.name == "Jupyter Notebook" else truncate(lang.name, 14)
        lang_items.append(
            f"""
    <circle cx="{dot_x}" cy="{y - 4}" r="3.5" fill="{escape(lang.color)}" />
    <text x="{name_x}" y="{y}" class="lang-name">{escape(display_name)}</text>
    <text x="{pct_x}" y="{y}" text-anchor="end" class="lang-pct">{lang.percent:.1f}%</text>"""
        )

    primary_lang = stats.languages[0].name if stats.languages else "—"
    if primary_lang == "Jupyter Notebook":
        primary_lang = "Jupyter"

    desc = (
        f"{stats.name}'s GitHub Activity: {stats.stars} stars, "
        f"{stats.commits} commits, {stats.prs} PRs, {stats.issues} issues."
    )

    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" fill="none" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{escape(desc)}">
  <title>{escape(stats.name)}'s GitHub Overview</title>
  <defs>
    <clipPath id="lang-bar-clip">
      <rect x="{bar_x:.1f}" y="{bar_y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" rx="4" />
    </clipPath>
  </defs>
  <style>
    .eyebrow {{
      font: 600 11px {FONT_SANS};
      letter-spacing: 0.07em;
      fill: {t["muted"]};
    }}
    .meta {{
      font: 400 11.5px {FONT_SANS};
      fill: {t["muted"]};
    }}
    .kpi-label {{
      font: 500 11.5px {FONT_SANS};
      fill: {t["secondary"]};
    }}
    .kpi-value {{
      font: 600 22px {FONT_SANS};
      fill: {t["text"]};
      font-variant-numeric: tabular-nums;
    }}
    .lang-name {{
      font: 500 12px {FONT_SANS};
      fill: {t["text"]};
    }}
    .lang-pct {{
      font: 400 11.5px {FONT_MONO};
      fill: {t["muted"]};
    }}
  </style>

  <!-- Left Card: GitHub Activity -->
  <rect x="0.5" y="0.5" width="411" height="213" rx="10" fill="{t["bg"]}" stroke="{t["border"]}" />

  <!-- Left Header -->
  <g transform="translate(24, 21)">
    <svg width="14" height="14" viewBox="0 0 16 16" fill="{t["muted"]}">
      <path d="M6 2c.306 0 .582.187.696.471L10 10.731l1.304-3.26A.751.751 0 0 1 12 7h3.25a.75.75 0 0 1 0 1.5h-2.742l-1.812 4.528a.751.751 0 0 1-1.392 0L6 4.77 4.696 8.03A.75.75 0 0 1 4 8.5H.75a.75.75 0 0 1 0-1.5h2.742l1.812-4.529A.751.751 0 0 1 6 2Z" />
    </svg>
  </g>
  <text x="45" y="32" class="eyebrow">GITHUB ACTIVITY</text>
  <text x="387" y="32" text-anchor="end" class="meta">Past 12 months</text>
  <line x1="24" y1="46" x2="387" y2="46" stroke="{t["divider"]}" />

  <!-- 2x2 KPI Crosshair -->
  <line x1="205.5" y1="56" x2="205.5" y2="164" stroke="{t["divider"]}" />
  <line x1="24" y1="110" x2="387" y2="110" stroke="{t["divider"]}" />

  <!-- KPI 1: Stars -->
  <g transform="translate(24, 57)">
    <svg width="13" height="13" viewBox="0 0 16 16" fill="{t["muted"]}">
      <path d="M8 .25a.75.75 0 0 1 .673.418l1.882 3.815 4.21.612a.75.75 0 0 1 .416 1.279l-3.046 2.97.719 4.192a.751.751 0 0 1-1.088.791L8 12.347l-3.766 1.98a.75.75 0 0 1-1.088-.79l.72-4.194L.818 6.374a.75.75 0 0 1 .416-1.28l4.21-.611L7.327.668A.75.75 0 0 1 8 .25Zm0 2.445L6.615 5.5a.75.75 0 0 1-.564.41l-3.097.45 2.24 2.184a.75.75 0 0 1 .216.664l-.528 3.084 2.769-1.456a.75.75 0 0 1 .698 0l2.77 1.456-.53-3.084a.75.75 0 0 1 .216-.664l2.24-2.183-3.096-.45a.75.75 0 0 1-.564-.41L8 2.694Z" />
    </svg>
    <text x="19" y="10.5" class="kpi-label">Stars Earned</text>
    <text x="0" y="39" class="kpi-value">{escape(format_compact(stats.stars))}</text>
  </g>

  <!-- KPI 2: Commits -->
  <g transform="translate(222, 57)">
    <svg width="13" height="13" viewBox="0 0 16 16" fill="{t["muted"]}">
      <path d="M11.93 8.5a4.002 4.002 0 0 1-7.86 0H.75a.75.75 0 0 1 0-1.5h3.32a4.002 4.002 0 0 1 7.86 0h3.32a.75.75 0 0 1 0 1.5Zm-1.43-.75a2.5 2.5 0 1 0-5 0 2.5 2.5 0 0 0 5 0Z" />
    </svg>
    <text x="19" y="10.5" class="kpi-label">Commits</text>
    <text x="0" y="39" class="kpi-value">{escape(format_compact(stats.commits))}</text>
  </g>

  <!-- KPI 3: Pull Requests -->
  <g transform="translate(24, 121)">
    <svg width="13" height="13" viewBox="0 0 16 16" fill="{t["muted"]}">
      <path d="M1.5 3.25a2.25 2.25 0 1 1 3 2.122v5.256a2.251 2.251 0 1 1-1.5 0V5.372A2.25 2.25 0 0 1 1.5 3.25Zm5.677-.177L9.573.677A.25.25 0 0 1 10 .854V2.5h1A2.5 2.5 0 0 1 13.5 5v5.628a2.251 2.251 0 1 1-1.5 0V5a1 1 0 0 0-1-1h-1v1.646a.25.25 0 0 1-.427.177L7.177 3.427a.25.25 0 0 1 0-.354ZM3.75 2.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm0 9.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm8.25.75a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0Z" />
    </svg>
    <text x="19" y="10.5" class="kpi-label">Pull Requests</text>
    <text x="0" y="39" class="kpi-value">{escape(format_compact(stats.prs))}</text>
  </g>

  <!-- KPI 4: Issues -->
  <g transform="translate(222, 121)">
    <svg width="13" height="13" viewBox="0 0 16 16" fill="{t["muted"]}">
      <path d="M8 9.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z" />
      <path d="M8 0a8 8 0 1 1 0 16A8 8 0 0 1 8 0ZM1.5 8a6.5 6.5 0 1 0 13 0 6.5 6.5 0 0 0-13 0Z" />
    </svg>
    <text x="19" y="10.5" class="kpi-label">Issues</text>
    <text x="0" y="39" class="kpi-value">{escape(format_compact(stats.issues))}</text>
  </g>

  <!-- Left Footer -->
  <line x1="24" y1="175" x2="387" y2="175" stroke="{t["divider"]}" />
  <text x="24" y="196" class="meta">Contributed to {stats.contributed_to} repos</text>
  <text x="387" y="196" text-anchor="end" class="meta">{stats.followers} followers</text>

  <!-- Right Card: Top Languages -->
  <rect x="428.5" y="0.5" width="411" height="213" rx="10" fill="{t["bg"]}" stroke="{t["border"]}" />

  <!-- Right Header -->
  <g transform="translate(452, 21)">
    <svg width="14" height="14" viewBox="0 0 16 16" fill="{t["muted"]}">
      <path d="m11.28 3.22 4.25 4.25a.75.75 0 0 1 0 1.06l-4.25 4.25a.749.749 0 0 1-1.275-.326.749.749 0 0 1 .215-.734L13.94 8l-3.72-3.72a.749.749 0 0 1 .326-1.275.749.749 0 0 1 .734.215Zm-6.56 0a.751.751 0 0 1 1.042.018.751.751 0 0 1 .018 1.042L2.06 8l3.72 3.72a.749.749 0 0 1-.326 1.275.749.749 0 0 1-.734-.215L.47 8.53a.75.75 0 0 1 0-1.06Z" />
    </svg>
  </g>
  <text x="473" y="32" class="eyebrow">TOP LANGUAGES</text>
  <text x="815" y="32" text-anchor="end" class="meta">Public repos</text>
  <line x1="452" y1="46" x2="815" y2="46" stroke="{t["divider"]}" />

  <!-- Segmented Language Bar -->
  <rect x="{bar_x:.1f}" y="{bar_y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" rx="4" fill="{t["bar_bg"]}" />
  <g clip-path="url(#lang-bar-clip)">
    {"".join(bar_rects)}
  </g>

  <!-- Language Legend (2x4) -->
  <g>{"".join(lang_items)}
  </g>

  <!-- Right Footer -->
  <line x1="452" y1="175" x2="815" y2="175" stroke="{t["divider"]}" />
  <text x="452" y="196" class="meta">Primary · {escape(primary_lang)}</text>
  <text x="815" y="196" text-anchor="end" class="meta">Top {len(stats.languages)} by size</text>
</svg>
"""


def render_featured_svg(repo: FeaturedRepo, theme_name: str) -> str:
    t = THEMES[theme_name]
    width, height = 840, 118

    # Position badges neatly right after "<owner> / <name>"
    owner_text = f"{repo.owner} / "
    title_x = 48.0
    title_end_x = (
        title_x
        + estimate_text_width(owner_text, 14.5)
        + estimate_text_width(repo.name, 15.0)
        + 12.0
    )

    header_pills: list[str] = []
    pill_x = title_end_x
    if repo.release_tag:
        pw = estimate_text_width(repo.release_tag, 11.0, mono=True) + 16.0
        header_pills.append(
            f'<rect x="{pill_x:.1f}" y="18.5" width="{pw:.1f}" height="20" rx="10" '
            f'fill="{t["tag_bg"]}" />'
            f'<text x="{pill_x + pw / 2:.1f}" y="32" text-anchor="middle" class="release-pill">'
            f"{escape(repo.release_tag)}</text>"
        )
        pill_x += pw + 6.0

    if repo.license_id:
        pw = estimate_text_width(repo.license_id, 10.5, mono=True) + 14.0
        header_pills.append(
            f'<rect x="{pill_x:.1f}" y="18.5" width="{pw:.1f}" height="20" rx="10" '
            f'fill="{t["pill_bg"]}" stroke="{t["pill_border"]}" />'
            f'<text x="{pill_x + pw / 2:.1f}" y="32" text-anchor="middle" class="meta-pill">'
            f"{escape(repo.license_id)}</text>"
        )

    # Bottom-left metadata row: primary language + topic pills
    lang_end_x = 40.0 + estimate_text_width(repo.language_name, 12.0) + 14.0
    topic_pills: list[str] = []
    tx = lang_end_x
    max_topic_x = 588.0
    for topic in repo.topics:
        tw = estimate_text_width(topic, 11.0) + 14.0
        if tx + tw > max_topic_x:
            break
        topic_pills.append(
            f'<rect x="{tx:.1f}" y="77" width="{tw:.1f}" height="20" rx="10" '
            f'fill="{t["pill_bg"]}" stroke="{t["pill_border"]}" />'
            f'<text x="{tx + tw / 2:.1f}" y="90.5" text-anchor="middle" class="topic-pill">'
            f"{escape(topic)}</text>"
        )
        tx += tw + 6.0

    desc_text = truncate(repo.description, 78)
    aria = f"{repo.owner}/{repo.name}: {repo.description} ({repo.stars} stars, {repo.forks} forks)"

    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" fill="none" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{escape(aria)}">
  <title>{escape(repo.owner)}/{escape(repo.name)}</title>
  <style>
    .repo-owner {{
      font: 400 14.5px {FONT_SANS};
      fill: {t["secondary"]};
    }}
    .repo-name {{
      font: 600 15px {FONT_SANS};
      fill: {t["accent"]};
    }}
    .repo-desc {{
      font: 400 13px {FONT_SANS};
      fill: {t["secondary"]};
    }}
    .release-pill {{
      font: 500 11px {FONT_MONO};
      fill: {t["tag_text"]};
    }}
    .meta-pill {{
      font: 500 10.5px {FONT_MONO};
      fill: {t["pill_text"]};
    }}
    .topic-pill {{
      font: 400 11px {FONT_SANS};
      fill: {t["pill_text"]};
    }}
    .lang-text {{
      font: 500 12px {FONT_SANS};
      fill: {t["text"]};
    }}
    .stat-label {{
      font: 500 11.5px {FONT_SANS};
      fill: {t["muted"]};
    }}
    .stat-value {{
      font: 600 21px {FONT_SANS};
      fill: {t["text"]};
      font-variant-numeric: tabular-nums;
    }}
  </style>

  <rect x="0.5" y="0.5" width="839" height="117" rx="10" fill="{t["bg"]}" stroke="{t["border"]}" />

  <!-- Left: Repo Identity -->
  <g transform="translate(24, 21)">
    <svg width="16" height="16" viewBox="0 0 16 16" fill="{t["muted"]}">
      <path d="M2 2.5A2.5 2.5 0 0 1 4.5 0h8.75a.75.75 0 0 1 .75.75v12.5a.75.75 0 0 1-.75.75h-2.5a.75.75 0 0 1 0-1.5h1.75v-2h-8a1 1 0 0 0-.714 1.7.75.75 0 1 1-1.072 1.05A2.495 2.495 0 0 1 2 11.5Zm10.5-1h-8a1 1 0 0 0-1 1v6.708A2.486 2.486 0 0 1 4.5 9h8ZM5 12.25a.25.25 0 0 1 .25-.25h3.5a.25.25 0 0 1 .25.25v3.25a.25.25 0 0 1-.4.2l-1.45-1.087a.249.249 0 0 0-.3 0L5.4 15.7a.25.25 0 0 1-.4-.2Z" />
    </svg>
  </g>
  <text x="{title_x:.0f}" y="33.5"><tspan class="repo-owner">{escape(owner_text)}</tspan><tspan class="repo-name">{escape(repo.name)}</tspan></text>
  {"".join(header_pills)}

  <!-- Description -->
  <text x="24" y="60" class="repo-desc">{escape(desc_text)}</text>

  <!-- Language & Topics -->
  <circle cx="29" cy="87" r="4" fill="{escape(repo.language_color)}" />
  <text x="40" y="91" class="lang-text">{escape(repo.language_name)}</text>
  {"".join(topic_pills)}

  <!-- Right: Stats Column -->
  <line x1="612.5" y1="20" x2="612.5" y2="98" stroke="{t["divider"]}" />

  <!-- Stars -->
  <g transform="translate(638, 36)">
    <svg width="13" height="13" viewBox="0 0 16 16" fill="{t["muted"]}">
      <path d="M8 .25a.75.75 0 0 1 .673.418l1.882 3.815 4.21.612a.75.75 0 0 1 .416 1.279l-3.046 2.97.719 4.192a.751.751 0 0 1-1.088.791L8 12.347l-3.766 1.98a.75.75 0 0 1-1.088-.79l.72-4.194L.818 6.374a.75.75 0 0 1 .416-1.28l4.21-.611L7.327.668A.75.75 0 0 1 8 .25Zm0 2.445L6.615 5.5a.75.75 0 0 1-.564.41l-3.097.45 2.24 2.184a.75.75 0 0 1 .216.664l-.528 3.084 2.769-1.456a.75.75 0 0 1 .698 0l2.77 1.456-.53-3.084a.75.75 0 0 1 .216-.664l2.24-2.183-3.096-.45a.75.75 0 0 1-.564-.41L8 2.694Z" />
    </svg>
    <text x="19" y="10.5" class="stat-label">Stars</text>
    <text x="0" y="39" class="stat-value">{escape(format_compact(repo.stars))}</text>
  </g>

  <!-- Forks -->
  <g transform="translate(734, 36)">
    <svg width="13" height="13" viewBox="0 0 16 16" fill="{t["muted"]}">
      <path d="M5 5.372v.878c0 .414.336.75.75.75h4.5a.75.75 0 0 0 .75-.75v-.878a2.25 2.25 0 1 1 1.5 0v.878a2.25 2.25 0 0 1-2.25 2.25h-1.5v2.128a2.251 2.251 0 1 1-1.5 0V8.5h-1.5A2.25 2.25 0 0 1 3.5 6.25v-.878a2.25 2.25 0 1 1 1.5 0ZM5 3.25a.75.75 0 1 0-1.5 0 .75.75 0 0 0 1.5 0Zm6.75.75a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5Zm-3 8.75a.75.75 0 1 0-1.5 0 .75.75 0 0 0 1.5 0Z" />
    </svg>
    <text x="19" y="10.5" class="stat-label">Forks</text>
    <text x="0" y="39" class="stat-value">{escape(format_compact(repo.forks))}</text>
  </g>

  <!-- Subtle External Indicator -->
  <g transform="translate(804, 18)">
    <svg width="14" height="14" viewBox="0 0 16 16" fill="{t["muted"]}">
      <path d="M3.75 2h3.5a.75.75 0 0 1 0 1.5h-3.5a.25.25 0 0 0-.25.25v8.5c0 .138.112.25.25.25h8.5a.25.25 0 0 0 .25-.25v-3.5a.75.75 0 0 1 1.5 0v3.5A1.75 1.75 0 0 1 12.25 14h-8.5A1.75 1.75 0 0 1 2 12.25v-8.5C2 2.784 2.784 2 3.75 2Zm6.854-1h4.146a.25.25 0 0 1 .25.25v4.146a.25.25 0 0 1-.427.177L13.03 4.03 9.28 7.78a.751.751 0 0 1-1.042-.018.751.751 0 0 1-.018-1.042l3.75-3.75-1.543-1.543A.25.25 0 0 1 10.604 1Z" />
    </svg>
  </g>
</svg>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate GitHub profile SVG cards.")
    parser.add_argument("--username", default=os.environ.get("GITHUB_USERNAME", "cauyxy"))
    parser.add_argument("--featured", default="BazaarPlusPlus/BazaarPlusPlus")
    parser.add_argument("--output-dir", default="profile")
    parser.add_argument(
        "--public-only",
        action="store_true",
        help="Restrict commit/PR/issue counts to public repositories (useful for local runs).",
    )
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("Error: GITHUB_TOKEN or GH_TOKEN environment variable is required.", file=sys.stderr)
        return 1

    featured_owner, featured_repo = args.featured.split("/", 1)
    stats, featured = fetch_data(
        token=token,
        username=args.username,
        featured_owner=featured_owner,
        featured_repo=featured_repo,
        public_only=args.public_only,
    )

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for theme_name in ("light", "dark"):
        (out_dir / f"overview-{theme_name}.svg").write_text(
            render_overview_svg(stats, theme_name), encoding="utf-8"
        )
        (out_dir / f"featured-{theme_name}.svg").write_text(
            render_featured_svg(featured, theme_name), encoding="utf-8"
        )

    print(
        f"Generated cards for @{stats.login} "
        f"(stars={stats.stars}, commits={stats.commits}, prs={stats.prs}, issues={stats.issues}; "
        f"featured={featured.owner}/{featured.name} ★{featured.stars})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
