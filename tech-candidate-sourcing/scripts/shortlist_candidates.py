#!/usr/bin/env python3
"""Create a review shortlist and suggested email allowlist from a candidate table.

This script does not approve or send outreach. It only suggests a smaller set
for human review before using approve_outreach_queue.py.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from md_table import escape_md, parse_path
from china_signals import has_china_public_signal


PREFERRED_CHANNELS = {"academic_web", "github_profile", "public_web", "platform_import"}
CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
RECOMMENDATION_RANK = {"不建议": 0, "待评估": 1, "待确认": 1, "备选": 2, "可聊": 3, "推荐": 4, "强推": 5, "强推荐": 5}
def parse_candidate_table(path: Path) -> list[dict[str, str]]:
    _headers, rows = parse_path(path)
    return [row for row in rows if row.get("邮箱")]


def numeric_score(value: str) -> int:
    match = re.search(r"\d+", value or "")
    return int(match.group(0)) if match else 0


def first_email(value: str) -> str:
    return re.split(r";|<br>", value or "")[0].strip()


def china_focus_signal(row: dict[str, str]) -> bool:
    return has_china_public_signal(" ".join(row.values()))


def shortlist_score(row: dict[str, str], focus: str = "general") -> int:
    score = numeric_score(row.get("匹配分", ""))
    confidence = row.get("置信度", "")
    recommendation = row.get("推荐级别", "")
    channels = {part.strip() for part in re.split(r";|<br>", row.get("渠道", "")) if part.strip()}
    if channels & PREFERRED_CHANNELS:
        score += 12
    if confidence == "medium":
        score += 8
    if confidence == "high":
        score += 12
    score += RECOMMENDATION_RANK.get(recommendation, 0) * 3
    if channels == {"github_commit"}:
        score -= 12
    if focus == "china" and china_focus_signal(row):
        score += 18
    return score


def eligible(row: dict[str, str], min_score: int, min_confidence: str, include_low: bool) -> bool:
    confidence = row.get("置信度", "")
    if not include_low and CONFIDENCE_RANK.get(confidence, 0) < CONFIDENCE_RANK.get(min_confidence, 0):
        return False
    if numeric_score(row.get("匹配分", "")) < min_score:
        return False
    return bool(first_email(row.get("邮箱", "")))


def select_rows(rows: list[dict[str, str]], limit: int, min_score: int, min_confidence: str, include_low: bool, focus: str = "general", require_china_signal: bool = False) -> list[dict[str, str]]:
    filtered = [row for row in rows if eligible(row, min_score, min_confidence, include_low)]
    if require_china_signal:
        filtered = [row for row in filtered if china_focus_signal(row)]
    if focus == "china":
        priority = [row for row in filtered if china_focus_signal(row)]
        fallback = [row for row in filtered if not china_focus_signal(row)]
        ranked_priority = sorted(priority, key=lambda row: shortlist_score(row, focus), reverse=True)
        ranked_fallback = sorted(fallback, key=lambda row: shortlist_score(row, focus), reverse=True)
        return (ranked_priority + ranked_fallback)[:limit]
    return sorted(filtered, key=lambda row: shortlist_score(row, focus), reverse=True)[:limit]


def render_markdown(rows: list[dict[str, str]], source: Path, min_score: int, min_confidence: str, focus: str) -> str:
    lines = [
        "# 候选人优先审核短名单",
        "",
        f"- 来源候选表: `{source}`",
        f"- 短名单人数: {len(rows)}",
        f"- 默认规则: 匹配分 >= {min_score}, 置信度 >= {min_confidence}, 优先 academic/profile/public/platform 来源。",
        f"- Focus: `{focus}`; `china` means China-affiliated or Chinese-language public evidence is prioritized, not protected identity inference.",
        "- 说明: 这是建议审核名单，不代表已批准发送；发送前仍需人工确认身份、意向、地点、薪资和联系方式归属。",
        "",
        "| 排名 | 姓名 | 邮箱 | 渠道 | 置信度 | 推荐级别 | 匹配分 | 研究方向 | 代表作/项目证据 | 中国相关公开信号 | 推荐点 | 风险点/待确认 | 来源 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for idx, row in enumerate(rows, start=1):
        cells = [
            str(idx),
            row.get("姓名", ""),
            first_email(row.get("邮箱", "")),
            row.get("渠道", ""),
            row.get("置信度", ""),
            row.get("推荐级别", ""),
            row.get("匹配分", ""),
            row.get("研究方向", ""),
            row.get("代表作/项目证据", ""),
            row.get("中国相关公开信号", ""),
            row.get("推荐点", ""),
            row.get("风险点/待确认", ""),
            row.get("来源", ""),
        ]
        lines.append("| " + " | ".join(escape_md(value) for value in cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a review shortlist and suggested allowlist from a candidate table.")
    parser.add_argument("--candidates", required=True, type=Path, help="Merged candidate Markdown table.")
    parser.add_argument("--out", required=True, type=Path, help="Output shortlist Markdown.")
    parser.add_argument("--allowlist-out", default=None, type=Path, help="Optional suggested allowlist text file.")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--min-score", type=int, default=70)
    parser.add_argument("--min-confidence", choices=["low", "medium", "high"], default="medium")
    parser.add_argument("--include-low", action="store_true", help="Allow low-confidence rows into the shortlist.")
    parser.add_argument("--focus", choices=["general", "china"], default="general", help="Prioritize China-affiliated or Chinese-language public evidence.")
    parser.add_argument("--require-china-signal", action="store_true", help="Hard-filter to explicit public China-related evidence; does not infer nationality or ethnicity.")
    args = parser.parse_args()

    rows = parse_candidate_table(args.candidates)
    selected = select_rows(rows, args.limit, args.min_score, args.min_confidence, args.include_low, args.focus, args.require_china_signal)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_markdown(selected, args.candidates, args.min_score, args.min_confidence, args.focus), encoding="utf-8")
    if args.allowlist_out:
        args.allowlist_out.parent.mkdir(parents=True, exist_ok=True)
        args.allowlist_out.write_text("\n".join(first_email(row.get("邮箱", "")) for row in selected) + "\n", encoding="utf-8")
    print(f"Wrote {len(selected)} shortlisted candidates to {args.out}")
    if args.allowlist_out:
        print(f"Wrote suggested allowlist to {args.allowlist_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
