#!/usr/bin/env python3
"""Generate multi-channel sourcing queries from a JD.

This script does not scrape logged-in platforms. It creates reviewable search
queries and URLs for public web, GitHub, academic, paper, and recruiting/social
channels so a sourcer or future authorized connector can continue coverage.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import quote_plus

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from jd_utils import jd_keywords
from md_table import escape_md


def search_url(engine: str, query: str) -> str:
    encoded = quote_plus(query)
    if engine == "github_repos":
        return f"https://github.com/search?q={encoded}&type=repositories"
    if engine == "github_users":
        return f"https://github.com/search?q={encoded}&type=users"
    if engine == "google_scholar":
        return f"https://scholar.google.com/scholar?q={encoded}"
    if engine == "linkedin":
        return f"https://www.linkedin.com/search/results/people/?keywords={encoded}"
    return f"https://www.bing.com/search?q={encoded}"


def build_queries(keywords: list[str], location: str) -> list[dict[str, str]]:
    top = keywords[:5]
    quoted = " ".join(f'"{kw}"' for kw in top[:3])
    broad = " ".join(top[:4])
    loc = f" {location}" if location else ""
    queries: list[dict[str, str]] = []

    templates = [
        ("academic_lab", "Bing", f'site:edu ({quoted}) ("people" OR "students" OR "lab") email'),
        ("academic_lab_cn", "Bing", f'site:edu.cn ({quoted}) ("学生" OR "博士生" OR "团队" OR "实验室") ("邮箱" OR "email")'),
        ("academic_lab_uk", "Bing", f'site:ac.uk ({quoted}) ("people" OR "students" OR "lab") email'),
        ("academic_faculty", "Bing", f'site:edu ({quoted}) ("professor" OR "faculty") email'),
        ("academic_faculty_cn", "Bing", f'site:edu.cn ({quoted}) ("教授" OR "老师" OR "导师") ("邮箱" OR "email")'),
        ("personal_homepage", "Bing", f'({quoted}) ("homepage" OR "Google Scholar") email'),
        ("github_repo", "GitHub repositories", f'{broad}'),
        ("github_user", "GitHub users", f'{broad} developer researcher'),
        ("paper_author", "Google Scholar", f'{broad}'),
        ("linkedin_public", "LinkedIn", f'{broad}{loc}'),
    ]

    engine_map = {
        "GitHub repositories": "github_repos",
        "GitHub users": "github_users",
        "Google Scholar": "google_scholar",
        "LinkedIn": "linkedin",
    }

    for channel, label, query in templates:
        engine = engine_map.get(label, "bing")
        queries.append(
            {
                "channel": channel,
                "label": label,
                "query": query,
                "url": search_url(engine, query),
            }
        )
    return queries


def render_markdown(jd_path: Path, keywords: list[str], rows: list[dict[str, str]]) -> str:
    lines = [
        "# 多渠道候选人寻访 Query 清单",
        "",
        f"- JD: `{jd_path}`",
        f"- 关键词: {', '.join(keywords) if keywords else '未提取到关键词'}",
        "- 说明: 这些是检索入口，不会登录招聘平台、不会绕过反爬、不会抓取非公开数据。",
        "",
        "| 渠道 | 平台 | Query | 入口 | 用法 |",
        "| --- | --- | --- | --- | --- |",
    ]
    usage = {
        "academic_lab": "优先找 lab people/students 页面，可作为 `--seed-url` 输入候选表脚本。",
        "academic_lab_cn": "优先找国内高校实验室、团队成员、博士生/学生页面，可作为 `--seed-url` 输入候选表脚本。",
        "academic_lab_uk": "优先找英国高校 lab people/students 页面，可作为 `--seed-url` 输入候选表脚本。",
        "academic_faculty": "找老师/PI，再从主页或 lab 页面追学生。",
        "academic_faculty_cn": "找国内高校老师/导师/PI，再从主页或实验室页面追学生。",
        "personal_homepage": "找个人主页和公开邮箱。",
        "github_repo": "找相关开源项目，再跑候选表脚本抓 contributor/profile 线索。",
        "github_user": "找 GitHub 用户，适合人工补 seed。",
        "paper_author": "找论文作者，再反查主页/邮箱。",
        "linkedin_public": "只作人工/授权平台检索入口，不自动抓取。",
    }
    for row in rows:
        channel = row["channel"]
        lines.append(
            "| "
            + " | ".join(
                [
                    channel,
                    row["label"],
                    escape_md(row["query"]),
                    f"[open]({row['url']})",
                    usage.get(channel, "人工复核后使用。"),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate multi-channel sourcing queries from a JD.")
    parser.add_argument("--jd", required=True, help="JD markdown/text file.")
    parser.add_argument("--out", required=True, help="Output Markdown file.")
    parser.add_argument("--location", default="", help="Optional location keyword, e.g. Beijing or Remote.")
    parser.add_argument("--max-keywords", type=int, default=8, help="Maximum JD keywords to use.")
    args = parser.parse_args()

    jd_path = Path(args.jd)
    jd_text = jd_path.read_text(encoding="utf-8")
    keywords = jd_keywords(jd_text, max_keywords=args.max_keywords)
    rows = build_queries(keywords, args.location)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(jd_path, keywords, rows), encoding="utf-8")
    print(f"Wrote {len(rows)} channel queries to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
