#!/usr/bin/env python3
"""Merge standard candidate Markdown tables and deduplicate by email."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from md_table import clean_cell, escape_md, parse_markdown
from candidate_schema import STANDARD_HEADERS, emails_from

COMBINE_FIELDS = {
    "渠道",
    "线索类型",
    "来源",
    "命中关键词",
    "推荐点",
    "风险点/待确认",
    "建议动作",
    "研究方向",
    "代表作/项目证据",
    "中国相关公开信号",
}
CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
RECOMMENDATION_RANK = {
    "不建议": 0,
    "待评估": 1,
    "待确认": 1,
    "备选": 2,
    "可聊": 3,
    "推荐": 4,
    "强推": 5,
    "强推荐": 5,
}


def parse_table(markdown: str) -> list[dict[str, str]]:
    _headers, rows = parse_markdown(markdown)
    return [row for row in rows if row.get("邮箱")]


def unique_join(*values: str) -> str:
    parts: list[str] = []
    for value in values:
        for part in re.split(r";|<br>", value or ""):
            part = clean_cell(part)
            if part and part not in parts and part not in {"暂无", "待补充"}:
                parts.append(part)
    return "<br>".join(parts)


def best_confidence(a: str, b: str) -> str:
    return a if CONFIDENCE_RANK.get(a, 0) >= CONFIDENCE_RANK.get(b, 0) else b


def best_recommendation(a: str, b: str) -> str:
    return a if RECOMMENDATION_RANK.get(a, 0) >= RECOMMENDATION_RANK.get(b, 0) else b


def best_score(a: str, b: str) -> str:
    def parse(value: str) -> int:
        match = re.search(r"\d+", value or "")
        return int(match.group(0)) if match else -1

    return a if parse(a) >= parse(b) else b


def prefer_richer(a: str, b: str) -> str:
    a = clean_cell(a)
    b = clean_cell(b)
    if not a or a in {"暂无", "待补充", "见来源页"}:
        return b
    if not b or b in {"暂无", "待补充", "见来源页"}:
        return a
    return a if len(a) >= len(b) else b


def merge_row(existing: dict[str, str], incoming: dict[str, str]) -> dict[str, str]:
    merged = dict(existing)
    for header in STANDARD_HEADERS:
        current = merged.get(header, "")
        new = incoming.get(header, "")
        if header in COMBINE_FIELDS:
            merged[header] = unique_join(current, new) or current or new
        elif header == "邮箱":
            merged[header] = unique_join(current, new).replace("<br>", "; ") or current or new
        elif header == "电话":
            merged[header] = unique_join(current, new).replace("<br>", "; ") or current or new or "暂无"
        elif header == "置信度":
            merged[header] = best_confidence(current, new)
        elif header == "推荐级别":
            merged[header] = best_recommendation(current, new)
        elif header == "匹配分":
            merged[header] = best_score(current, new)
        else:
            merged[header] = prefer_richer(current, new)
    return merged


def merge_tables(paths: list[Path]) -> list[dict[str, str]]:
    groups: dict[int, dict[str, str]] = {}
    email_to_group: dict[str, int] = {}
    next_group = 0
    for path in paths:
        for row in parse_table(path.read_text(encoding="utf-8")):
            emails = emails_from(row.get("邮箱", ""))
            if not emails:
                continue
            normalized = {header: row.get(header, "") for header in STANDARD_HEADERS}
            matched_groups = {email_to_group[email] for email in emails if email in email_to_group}
            if matched_groups:
                target = min(matched_groups)
                merged = groups[target]
                for other in sorted(matched_groups - {target}):
                    merged = merge_row(merged, groups.pop(other))
                    for known_email, group_id in list(email_to_group.items()):
                        if group_id == other:
                            email_to_group[known_email] = target
                groups[target] = merge_row(merged, normalized)
            else:
                target = next_group
                next_group += 1
                groups[target] = normalized
            for email in emails_from(groups[target].get("邮箱", "")):
                email_to_group[email] = target
    return sorted(
        groups.values(),
        key=lambda row: (-CONFIDENCE_RANK.get(row.get("置信度", ""), 0), row.get("姓名", "").lower()),
    )


def render_table(rows: list[dict[str, str]], inputs: list[Path], title: str) -> str:
    lines = [
        f"# {title}",
        "",
        f"- 输入表数量: {len(inputs)}",
        f"- 去重后候选人数: {len(rows)}",
        "- 去重规则: 任一公开邮箱重叠即合并，保留所有邮箱及来源、关键词、风险和动作字段的多来源证据。",
        "",
        "| " + " | ".join(STANDARD_HEADERS) + " |",
        "| " + " | ".join(["---"] * len(STANDARD_HEADERS)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(escape_md(row.get(header, "")) for header in STANDARD_HEADERS) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge candidate Markdown tables and deduplicate by email.")
    parser.add_argument("--input", action="append", required=True, help="Candidate Markdown table. Repeatable.")
    parser.add_argument("--out", required=True, help="Merged output Markdown path.")
    parser.add_argument("--title", default="合并候选人标准表", help="Output Markdown title.")
    args = parser.parse_args()

    input_paths = [Path(value) for value in args.input]
    rows = merge_tables(input_paths)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_table(rows, input_paths, args.title), encoding="utf-8")
    print(f"Wrote {len(rows)} merged candidates to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
