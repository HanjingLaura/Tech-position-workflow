#!/usr/bin/env python3
"""Normalize platform-exported candidates into the standard Markdown table.

Use this for CSV/TSV exports or copied tabular text from recruiting/social
platforms after the user has legitimate access. The script does not log in,
scrape platforms, or enrich private data.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from candidate_schema import EMAIL_RE, STANDARD_HEADERS
from md_table import render_rows

PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3,4}[\s.-]?\d{4}(?!\d)")

FIELD_ALIASES = {
    "name": ["姓名", "名字", "候选人", "candidate", "name", "full name", "姓名/页面名"],
    "email": ["邮箱", "邮件", "email", "e-mail", "mail", "公开邮箱"],
    "phone": ["电话", "手机号", "手机", "phone", "mobile", "tel"],
    "school": ["学校", "院校", "教育", "education", "school", "university"],
    "company": ["公司", "机构", "当前公司", "current company", "company", "organization", "org"],
    "title": ["职位", "岗位", "title", "position", "role"],
    "location": ["地点", "城市", "location", "city"],
    "source": ["来源", "source", "url", "profile", "主页", "链接", "link"],
    "notes": ["备注", "说明", "推荐点", "notes", "comment", "summary"],
    "keywords": ["关键词", "技能", "skills", "tags", "命中关键词"],
}


def normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def find_column(headers: list[str], field: str) -> str | None:
    normalized = {normalize_header(header): header for header in headers}
    for alias in FIELD_ALIASES[field]:
        key = normalize_header(alias)
        if key in normalized:
            return normalized[key]
    for header in headers:
        lowered = normalize_header(header)
        if any(normalize_header(alias) in lowered for alias in FIELD_ALIASES[field]):
            return header
    return None


def text_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        email = next(iter(EMAIL_RE.findall(line)), "")
        phone = next(iter(PHONE_RE.findall(line)), "")
        name = line.split(email)[0].strip(" ,-|\t") if email else line.strip()
        rows.append({"姓名": name, "邮箱": email, "电话": phone, "备注": line.strip()})
    return rows


def is_likely_tabular(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    return any(delimiter in lines[0] for delimiter in [",", "\t", ";", "|"])


def has_known_header(headers: list[str] | None) -> bool:
    if not headers:
        return False
    header_text = " ".join(normalize_header(header) for header in headers)
    aliases = [normalize_header(alias) for values in FIELD_ALIASES.values() for alias in values]
    return any(alias in header_text for alias in aliases)


def read_rows(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig")
    if not is_likely_tabular(text):
        return text_rows(text)

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    if has_known_header(reader.fieldnames):
        return [{k: (v or "").strip() for k, v in row.items()} for row in reader]
    return text_rows(text)


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def extract_email(value: str) -> str:
    emails = EMAIL_RE.findall(value or "")
    return emails[0] if emails else ""


def extract_phone(value: str) -> str:
    phones = PHONE_RE.findall(value or "")
    return phones[0].strip() if phones else ""


def join_info(*parts: str) -> str:
    seen = []
    for part in parts:
        part = clean(part)
        if part and part not in seen:
            seen.append(part)
    return "; ".join(seen) if seen else "待补充"


def normalize_rows(rows: list[dict[str, str]], platform: str, default_source: str) -> list[dict[str, str]]:
    if not rows:
        return []
    headers = list(rows[0].keys())
    columns = {field: find_column(headers, field) for field in FIELD_ALIASES}
    normalized: list[dict[str, str]] = []
    for row in rows:
        def get(field: str) -> str:
            col = columns.get(field)
            return row.get(col, "").strip() if col else ""

        row_text = " ".join(str(value or "") for value in row.values())
        email = extract_email(get("email")) or extract_email(row_text)
        if not email:
            continue
        name = clean(get("name")) or email.split("@", 1)[0]
        phone = extract_phone(get("phone")) or extract_phone(row_text) or "暂无"
        source = clean(get("source")) or default_source or platform
        keywords = clean(get("keywords")) or "平台导入，待人工补关键词"
        notes = clean(get("notes"))
        basic_info = join_info(get("school"), get("company"), get("title"), get("location"))
        normalized.append(
            {
                "姓名": name,
                "基础信息": basic_info,
                "电话": phone,
                "邮箱": email,
                "渠道": platform,
                "线索类型": "platform_import",
                "置信度": "medium",
                "推荐级别": "待评估",
                "匹配分": "未评分",
                "来源": source,
                "命中关键词": keywords,
                "推荐点": notes or "平台导入候选，需要结合 JD 人工确认匹配点",
                "风险点/待确认": "需确认候选人授权来源、当前状态、求职意向、地点、薪资和联系方式有效性",
                "建议动作": "人工复核后进入邮件草稿或电话确认",
                "研究方向": keywords,
                "代表作/项目证据": notes or source,
                "中国相关公开信号": "平台导入记录，需依据公开学校/机构/地区信息人工核验",
            }
        )
    return normalized


def render_table(rows: list[dict[str, str]], input_path: Path, platform: str) -> str:
    lines = [
        "# 平台导入候选人标准表",
        "",
        f"- 来源文件: `{input_path}`",
        f"- 平台/渠道: {platform}",
        f"- 候选人数: {len(rows)}",
        "- 说明: 该表来自用户提供的导出/复制数据，不自动登录或抓取招聘平台。",
        "",
    ]
    return "\n".join(lines) + "\n" + render_rows(STANDARD_HEADERS, rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import platform-exported candidates into the standard Markdown table.")
    parser.add_argument("--input", required=True, help="CSV/TSV/text file exported or copied from a platform.")
    parser.add_argument("--out", required=True, help="Output Markdown candidate table.")
    parser.add_argument("--platform", default="platform_import", help="Channel label, e.g. boss, liepin, maimai, linkedin.")
    parser.add_argument("--source", default="", help="Optional source URL or note for rows without source column.")
    args = parser.parse_args()

    input_path = Path(args.input)
    raw_rows = read_rows(input_path)
    normalized = normalize_rows(raw_rows, args.platform, args.source)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_table(normalized, input_path, args.platform), encoding="utf-8")
    print(f"Wrote {len(normalized)} imported candidates to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
