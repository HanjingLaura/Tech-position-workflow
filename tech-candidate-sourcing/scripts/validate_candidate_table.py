#!/usr/bin/env python3
"""Validate a standard candidate Markdown table and write a QA report."""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from md_table import clean_cell, parse_markdown
from candidate_schema import STANDARD_HEADERS as REQUIRED_HEADERS, emails_from as shared_emails_from, is_placeholder

BLOCKED_EMAIL_FRAGMENTS = [
    "example",
    "localhost",
    "noreply",
    "your_email",
    "yourname",
    "username@",
    "googlegroups.com",
    "ingest.sentry.io",
    "dmca",
]

INCOMPLETE_EMAIL_DOMAINS = {"cs.stanford", "cs.cornell", "csail.mit", "seas.harvard"}

SUSPICIOUS_NAME_TOKENS = [
    "admission",
    "administrative",
    "ai index",
    "center",
    "college of",
    "contact",
    "department",
    "directory",
    "exams",
    "faculty openings",
    "get involved",
    "graduate students",
    "homepage",
    "instagram photos",
    "lab",
    "news",
    "office",
    "people",
    "privacy",
    "program",
    "prospective students",
    "reading groups",
    "research publications",
    "school of",
    "southwest university",
    "staff",
    "student affinity",
    "student verification",
    "support",
    "team",
    "terms",
    "transfer credit",
    "undergraduate",
    "university of",
    "visitor information",
    "website",
    "研究中心",
    "研究院",
]


def parse_table(markdown: str) -> tuple[list[str], list[dict[str, str]]]:
    return parse_markdown(markdown)


def emails_from(value: str) -> list[str]:
    return shared_emails_from(value)


def bad_email(email: str) -> bool:
    lowered = email.lower()
    domain = lowered.split("@", 1)[1] if "@" in lowered else ""
    return domain in INCOMPLETE_EMAIL_DOMAINS or any(fragment in lowered for fragment in BLOCKED_EMAIL_FRAGMENTS)


def missing_or_placeholder(value: str) -> bool:
    return is_placeholder(value)


def looks_like_person_name(name: str) -> bool:
    name = clean_cell(name)
    if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", name):
        return True
    words = re.findall(r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'`.-]+", name)
    return 2 <= len(words) <= 6 and len(name) <= 90


def suspicious_non_person_name(name: str) -> bool:
    cleaned = clean_cell(name)
    lowered = cleaned.lower()
    if any(token in lowered for token in SUSPICIOUS_NAME_TOKENS):
        return True
    if "|" in cleaned and not looks_like_person_name(cleaned.split("|", 1)[0].strip()):
        return True
    return False


def channel_counts(rows: list[dict[str, str]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        channels = re.split(r";|<br>", row.get("渠道", ""))
        for channel in channels:
            channel = clean_cell(channel)
            if channel:
                counts[channel] += 1
    return counts


def render_counter(counter: Counter[str]) -> str:
    if not counter:
        return "none"
    return ", ".join(f"{key}: {value}" for key, value in sorted(counter.items()))


def validate(path: Path) -> dict[str, object]:
    try:
        headers, rows = parse_table(path.read_text(encoding="utf-8"))
        parse_error = ""
    except ValueError as exc:
        return {
            "status": "fail",
            "headers": [],
            "parse_error": str(exc),
            "missing_headers": REQUIRED_HEADERS,
            "row_count": 0,
            "unique_email_count": 0,
            "empty_table": True,
            "no_unique_emails": True,
            "rows_without_email": [],
            "rows_with_bad_email": [],
            "duplicate_emails": [],
            "rows_missing_basic_info": [],
            "rows_missing_source": [],
            "rows_missing_risk": [],
            "rows_missing_research_direction": [],
            "rows_missing_representative_evidence": [],
            "rows_suspicious_name": [],
            "channel_counts": Counter(),
            "confidence_counts": Counter(),
        }
    missing_headers = [header for header in REQUIRED_HEADERS if header not in headers]

    all_emails: list[str] = []
    rows_without_email = []
    rows_with_bad_email = []
    rows_missing_basic_info = []
    rows_missing_source = []
    rows_missing_risk = []
    rows_missing_research_direction = []
    rows_missing_representative_evidence = []
    rows_suspicious_name = []

    for idx, row in enumerate(rows, start=1):
        emails = emails_from(row.get("邮箱", ""))
        if not emails:
            rows_without_email.append(idx)
        for email in emails:
            all_emails.append(email)
            if bad_email(email):
                rows_with_bad_email.append((idx, email))
        if missing_or_placeholder(row.get("基础信息", "")):
            rows_missing_basic_info.append(idx)
        if missing_or_placeholder(row.get("来源", "")):
            rows_missing_source.append(idx)
        if missing_or_placeholder(row.get("风险点/待确认", "")):
            rows_missing_risk.append(idx)
        if missing_or_placeholder(row.get("研究方向", "")):
            rows_missing_research_direction.append(idx)
        if missing_or_placeholder(row.get("代表作/项目证据", "")):
            rows_missing_representative_evidence.append(idx)
        if suspicious_non_person_name(row.get("姓名", "")):
            rows_suspicious_name.append(idx)

    email_counts = Counter(all_emails)
    duplicate_emails = sorted(email for email, count in email_counts.items() if count > 1)
    empty_table = len(rows) == 0
    no_unique_emails = len(email_counts) == 0
    confidence = Counter(clean_cell(row.get("置信度", "")) or "missing" for row in rows)

    status = "pass"
    if missing_headers or empty_table or no_unique_emails or rows_without_email:
        status = "fail"
    elif (
        rows_with_bad_email
        or
        duplicate_emails
        or rows_missing_basic_info
        or rows_missing_source
        or rows_missing_risk
        or rows_missing_research_direction
        or rows_missing_representative_evidence
        or rows_suspicious_name
    ):
        status = "needs_review"

    return {
        "status": status,
        "headers": headers,
        "parse_error": parse_error,
        "missing_headers": missing_headers,
        "row_count": len(rows),
        "unique_email_count": len(email_counts),
        "empty_table": empty_table,
        "no_unique_emails": no_unique_emails,
        "rows_without_email": rows_without_email,
        "rows_with_bad_email": rows_with_bad_email,
        "duplicate_emails": duplicate_emails,
        "rows_missing_basic_info": rows_missing_basic_info,
        "rows_missing_source": rows_missing_source,
        "rows_missing_risk": rows_missing_risk,
        "rows_missing_research_direction": rows_missing_research_direction,
        "rows_missing_representative_evidence": rows_missing_representative_evidence,
        "rows_suspicious_name": rows_suspicious_name,
        "channel_counts": channel_counts(rows),
        "confidence_counts": confidence,
    }


def list_or_none(values: list[object], limit: int = 20) -> str:
    if not values:
        return "none"
    shown = values[:limit]
    suffix = "" if len(values) <= limit else f", ... +{len(values) - limit} more"
    return ", ".join(str(value) for value in shown) + suffix


def render_report(path: Path, result: dict[str, object]) -> str:
    bad_email_rows = [f"row {idx}: {email}" for idx, email in result["rows_with_bad_email"]]  # type: ignore[index]
    return "\n".join(
        [
            "# Candidate Table Validation Report",
            "",
            f"- Candidate table: `{path}`",
            f"- Status: `{result['status']}`",
            f"- Rows: {result['row_count']}",
            f"- Unique emails: {result['unique_email_count']}",
            f"- Channel distribution: {render_counter(result['channel_counts'])}",  # type: ignore[arg-type]
            f"- Confidence distribution: {render_counter(result['confidence_counts'])}",  # type: ignore[arg-type]
            "",
            "## Structural Checks",
            "",
            f"- Markdown parse error: {result.get('parse_error') or 'none'}",
            f"- Missing required headers: {list_or_none(result['missing_headers'])}",  # type: ignore[arg-type]
            f"- Empty candidate table: {'yes' if result['empty_table'] else 'no'}",
            f"- No unique real emails: {'yes' if result['no_unique_emails'] else 'no'}",
            f"- Rows without real email: {list_or_none(result['rows_without_email'])}",  # type: ignore[arg-type]
            f"- Rows with blocked/service/placeholder emails: {list_or_none(bad_email_rows)}",
            f"- Duplicate emails: {list_or_none(result['duplicate_emails'])}",  # type: ignore[arg-type]
            "",
            "## Review Signals",
            "",
            f"- Rows missing basic info: {list_or_none(result['rows_missing_basic_info'])}",  # type: ignore[arg-type]
            f"- Rows missing source: {list_or_none(result['rows_missing_source'])}",  # type: ignore[arg-type]
            f"- Rows missing risk/confirmation notes: {list_or_none(result['rows_missing_risk'])}",  # type: ignore[arg-type]
            f"- Rows missing research direction: {list_or_none(result['rows_missing_research_direction'])}",  # type: ignore[arg-type]
            f"- Rows missing representative paper/project evidence: {list_or_none(result['rows_missing_representative_evidence'])}",  # type: ignore[arg-type]
            f"- Rows with suspicious non-person/page-title names: {list_or_none(result['rows_suspicious_name'])}",  # type: ignore[arg-type]
            "",
            "## Interpretation",
            "",
            "- `pass`: required structure and emails look usable for review.",
            "- `needs_review`: usable table, but some rows need manual cleanup before outreach.",
            "- `fail`: missing required structure, contains no candidate/email rows, or contains rows without real public emails.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a standard candidate Markdown table.")
    parser.add_argument("--candidates", required=True, help="Candidate Markdown table.")
    parser.add_argument("--out", required=True, help="Output Markdown validation report.")
    args = parser.parse_args()

    candidate_path = Path(args.candidates)
    result = validate(candidate_path)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_report(candidate_path, result), encoding="utf-8")
    print(f"Validation status: {result['status']}")
    print(f"Wrote validation report to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
