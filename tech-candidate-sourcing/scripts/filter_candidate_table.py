#!/usr/bin/env python3
"""Filter obvious non-person rows from a standard candidate Markdown table."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from md_table import clean_cell, escape_md as md_escape, parse_markdown
from china_signals import has_china_public_signal as text_has_china_public_signal
from candidate_schema import emails_from, is_placeholder


NON_PERSON_NAME_TOKENS = [
    "administrative",
    "admission",
    "admissions",
    "bair",
    "carnegie mellon university in qatar",
    "computing guide",
    "contact",
    "department",
    "directory",
    "faculty by",
    "home",
    "office",
    "people",
    "program",
    "research",
    "school of",
    "staff",
    "support",
    "team",
    "undergraduate",
    "university of",
]

HARD_NON_PERSON_NAME_TOKENS = [
    "administrative staff",
    "contact us",
    "curriculum",
    "exams",
    "faculty openings",
    "fellowship",
    "getting started",
    "graduate clubs",
    "graduate students",
    "incoming ph.d. student information",
    "international programs",
    "ischool directory",
    "institute communications",
    "joint machine learning ph.d. programs",
    "legal",
    "master of arts",
    "master's in machine learning",
    "news + updates",
    "ph.d. committees",
    "privacy statement",
    "prospective students",
    "reading groups and ml courses",
    "research publications",
    "student awards",
    "student affinity groups",
    "student verification",
    "technical staff",
    "the department of computer science",
    "terms and conditions",
    "terms of use",
    "undergraduate concentration",
    "undergraduate minor",
    "transfer credit",
    "visitor information",
    "website terms",
    "instagram photos and videos",
    "研究中心",
    "人工智能研究院",
]

GENERIC_EMAIL_LOCAL_PARTS = {
    "admin",
    "admission",
    "admissions",
    "bair-admin",
    "bair-website",
    "contact",
    "cs",
    "csstaff",
    "dailydigest",
    "dmca",
    "ejemplo",
    "ess",
    "everification",
    "gmmtv.artists",
    "grouptours",
    "hai-policy",
    "help",
    "info",
    "jobs",
    "mail",
    "mldweb",
    "mlphdprogram",
    "office",
    "awards",
    "mylastname",
    "privacy",
    "staff",
    "support",
    "teamagh",
    "teamaisd",
    "teameptx",
    "teamhhs",
    "teamiis",
    "teamlafe",
    "teamvhd",
    "teamwtpi",
    "ug-admission",
    "uwdmca",
    "webmaster",
    "aipku",
}

INSTITUTION_HINTS = [
    ("cs.stanford.edu", "Stanford University"),
    ("stanford.edu", "Stanford University"),
    ("csail.mit.edu", "MIT CSAIL"),
    ("media.mit.edu", "MIT Media Lab"),
    ("mit.edu", "MIT"),
    ("princeton.edu", "Princeton University"),
    ("cs.cornell.edu", "Cornell University"),
    ("cornell.edu", "Cornell University"),
    ("berkeley.edu", "UC Berkeley"),
    ("cs.washington.edu", "University of Washington"),
    ("washington.edu", "University of Washington"),
    ("uw.edu", "University of Washington"),
    ("umiacs.umd.edu", "University of Maryland"),
    ("umd.edu", "University of Maryland"),
    ("illinois.edu", "University of Illinois Urbana-Champaign"),
    ("ucla.edu", "UCLA"),
    ("upenn.edu", "University of Pennsylvania"),
    ("jhu.edu", "Johns Hopkins University"),
    ("cc.gatech.edu", "Georgia Tech"),
    ("gatech.edu", "Georgia Tech"),
    ("andrew.cmu.edu", "Carnegie Mellon University"),
    ("cmu.edu", "Carnegie Mellon University"),
    ("ucsb.edu", "UC Santa Barbara"),
    ("emory.edu", "Emory University"),
    ("utexas.edu", "University of Texas at Austin"),
    ("northeastern.edu", "Northeastern University"),
    ("schmidtsciences.org", "Schmidt Sciences"),
    ("boltz.bio", "Boltz Bio"),
    ("pku.edu.cn", "Peking University"),
]


def parse_table(markdown: str) -> tuple[list[str], list[dict[str, str]]]:
    return parse_markdown(markdown)


def emails(value: str) -> list[str]:
    return emails_from(value)


def looks_like_person_name(name: str) -> bool:
    name = clean_cell(name)
    if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", name):
        return True
    words = re.findall(r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'`.-]+", name)
    return 2 <= len(words) <= 6 and len(name) <= 90


def name_from_email(email_value: str) -> str:
    for email in emails(email_value):
        local = email.split("@", 1)[0]
        if "." not in local and "_" not in local and "-" not in local:
            continue
        parts = [part for part in re.split(r"[._-]+", local) if part and not part.isdigit()]
        if 2 <= len(parts) <= 4 and all(re.fullmatch(r"[A-Za-z]{2,}", part) for part in parts):
            return " ".join(part.capitalize() for part in parts)
    return ""


def normalize_name(name: str, email_value: str = "") -> str:
    original = clean_cell(name)
    normalized = original
    replacements = [
        (r"^About\s+Me\s*[-–—]\s*", ""),
        (r"^About\s*[-–—]\s*", ""),
        (r"^Home\s+Page\s+of\s+", ""),
        (r"^Home\s*[|｜:：–—-]\s*", ""),
        (r"^Overview\s*‹\s*", ""),
        (r"\s*[|｜]\s*Portfolio\s*$", ""),
        (r"\s*[|｜]\s*Massachusetts Institute of Technology\s*$", ""),
        (r"\s*[|｜]\s*MIT\s*$", ""),
        (r"\s*[|｜]\s*Ph\.?D\.?.*$", ""),
        (r"\s*[|｜]\s*Causality in Cognition Lab\s*$", ""),
        (r"\s*[—-]\s*MIT Media Lab\s*$", ""),
        (r"\s*,\s*Stanford NLP\s*$", ""),
        (r"\s*,\s*Stanford University\s*$", ""),
        (r"'s\s+Homepage\s*$", ""),
        (r"\s+Homepage\s*$", ""),
    ]
    for pattern, repl in replacements:
        normalized = re.sub(pattern, repl, normalized, flags=re.IGNORECASE)
    normalized = clean_cell(normalized.strip(" -–—|｜:："))

    if looks_like_person_name(normalized):
        return normalized

    inferred = name_from_email(email_value)
    if inferred and not looks_like_person_name(normalized):
        return inferred
    return normalized or original


def infer_institution(row: dict[str, str]) -> str:
    haystack = " ".join(
        [
            row.get("邮箱", ""),
            row.get("来源", ""),
            row.get("基础信息", ""),
        ]
    ).lower()
    for token, institution in INSTITUTION_HINTS:
        if token in haystack:
            return institution
    if "github" in row.get("渠道", "").lower():
        return "Public GitHub profile/commit metadata; affiliation needs review"
    return ""


def basic_info_needs_enrichment(value: str) -> bool:
    cleaned = clean_cell(value)
    return is_placeholder(cleaned) or len(cleaned) < 20


def enrich_row(row: dict[str, str]) -> dict[str, str]:
    enriched = dict(row)
    enriched["姓名"] = normalize_name(enriched.get("姓名", ""), enriched.get("邮箱", ""))

    basic_info = clean_cell(enriched.get("基础信息", ""))
    institution = infer_institution(enriched)
    if basic_info_needs_enrichment(basic_info) and institution:
        enriched["基础信息"] = f"{institution}; inferred from public email/source, needs manual review"
    elif institution and institution.lower() not in basic_info.lower():
        enriched["基础信息"] = f"{basic_info}; {institution}" if basic_info else institution
    return enriched


def is_generic_email(email: str) -> bool:
    local = email.split("@", 1)[0].lower()
    domain = email.split("@", 1)[1].lower() if "@" in email else ""
    incomplete_domains = {"cs.stanford", "cs.cornell", "csail.mit", "seas.harvard"}
    return (
        len(local) < 2
        or "mylastname" in local
        or local in GENERIC_EMAIL_LOCAL_PARTS
        or domain in incomplete_domains
        or "sentry" in domain
        or "admission" in local
    )


def keep_row(row: dict[str, str]) -> bool:
    name = clean_cell(row.get("姓名", ""))
    lower_name = name.lower()
    row_emails = emails(row.get("邮箱", ""))
    if not row_emails:
        return False
    if any(token in lower_name for token in HARD_NON_PERSON_NAME_TOKENS):
        return False
    if any(token in lower_name for token in NON_PERSON_NAME_TOKENS) and not looks_like_person_name(name):
        return False
    if len(row_emails) > 3 and not looks_like_person_name(name):
        return False
    if all(is_generic_email(email) for email in row_emails):
        return False
    return True


def has_china_public_signal(row: dict[str, str]) -> bool:
    return text_has_china_public_signal(" ".join(row.values()))


def render(headers: list[str], rows: list[dict[str, str]], source: Path, dropped: int) -> str:
    lines = [
        "# 清洗后候选人表",
        "",
        f"- Source table: `{source}`",
        f"- Kept rows: {len(rows)}",
        f"- Dropped by person/email/optional-China-signal filters: {dropped}",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(md_escape(row.get(header, "")) for header in headers) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Filter obvious non-person rows from a candidate Markdown table.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--require-china-signal", action="store_true", help="Keep only rows with explicit public China-related evidence; never infer nationality or ethnicity.")
    args = parser.parse_args()

    headers, rows = parse_table(args.input.read_text(encoding="utf-8"))
    kept = [
        enrich_row(row)
        for row in rows
        if keep_row(row) and (not args.require_china_signal or has_china_public_signal(row))
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(headers, kept, args.input, len(rows) - len(kept)), encoding="utf-8")
    print(f"Kept {len(kept)} candidates; dropped {len(rows) - len(kept)} rows by active filters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
