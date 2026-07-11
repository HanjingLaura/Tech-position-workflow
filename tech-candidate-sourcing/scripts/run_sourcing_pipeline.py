#!/usr/bin/env python3
"""Run the JD-to-candidate-table-to-outreach-queue MVP pipeline."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from md_table import clean_cell, parse_path
from candidate_schema import emails_from


_ACTIVE_CONTEXT: tuple[dict[str, Path], list[Path], argparse.Namespace] | None = None


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def run_step(command: list[str], timeout: int) -> None:
    print("$ " + " ".join(f'"{part}"' if " " in part else part for part in command))
    subprocess.run(command, check=True, timeout=timeout)


def safe_prefix(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
    return stem[:80] or "sourcing_run"


def platform_spec(value: str) -> tuple[str, Path]:
    if "=" in value:
        platform, path = value.split("=", 1)
        return safe_prefix(platform.lower()), Path(path)
    return "platform_import", Path(value)


def apply_coverage_preset(args: argparse.Namespace) -> None:
    if args.coverage_preset != "broad":
        return
    args.depth = "deep"
    args.search_limit = max(args.search_limit, 8)
    args.max_urls = max(args.max_urls, 180)
    args.github_user_limit = max(args.github_user_limit, 20)
    args.github_contributor_limit = max(args.github_contributor_limit, 35)
    args.github_commit_limit = max(args.github_commit_limit, 35)
    args.academic_link_limit = max(args.academic_link_limit, 35)
    args.paper_max_papers = max(args.paper_max_papers, 12)
    args.paper_max_conference_papers = max(args.paper_max_conference_papers, 20)
    args.dblp_author_profile_limit = max(args.dblp_author_profile_limit, 30)
    args.publication_pdf_limit = max(args.publication_pdf_limit, 50)
    args.paper_max_authors = max(args.paper_max_authors, 220)
    args.paper_seed_limit = max(args.paper_seed_limit, 60)
    args.paper_seeds_per_query = max(args.paper_seeds_per_query, 2)
    args.paper_max_openalex_papers = max(args.paper_max_openalex_papers, 40)
    args.publication_pdf_workers = max(args.publication_pdf_workers, 4)
    args.publication_pdf_max_pages = max(args.publication_pdf_max_pages, 3)
    args.step_timeout = max(args.step_timeout, 3600)


def build_source_command(args: argparse.Namespace, jd_path: Path, public_table: Path) -> list[str]:
    scripts = script_dir()
    command = [
        sys.executable,
        str(scripts / "source_candidates.py"),
        "--jd",
        str(jd_path),
        "--out",
        str(public_table),
        "--depth",
        args.depth,
        "--search-limit",
        str(args.search_limit),
        "--max-urls",
        str(args.max_urls),
        "--github-user-limit",
        str(args.github_user_limit),
        "--github-contributor-limit",
        str(args.github_contributor_limit),
        "--github-commit-limit",
        str(args.github_commit_limit),
        "--academic-link-limit",
        str(args.academic_link_limit),
        "--focus",
        args.focus,
        "--deadline-seconds",
        str(max(1, args.step_timeout - 60)),
    ]
    if args.require_china_signal:
        command.append("--require-china-signal")
    if args.collect_phones:
        command.append("--collect-phones")
    for url in args.seed_url:
        command.extend(["--seed-url", url])
    for seed_file in args.seed_file:
        command.extend(["--seed-file", str(seed_file)])
    if args.no_search:
        command.append("--no-search")
    if args.no_github_search:
        command.append("--no-github-search")
    if args.no_academic_expand:
        command.append("--no-academic-expand")
    return command


def add_suppression_args(command: list[str], args: argparse.Namespace) -> None:
    for path in args.suppression_list:
        command.extend(["--suppression-list", str(path)])


def report_status(path: Path) -> str:
    if not path.exists():
        return "missing"
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.search(r"^-\s+Status:\s+`?([A-Za-z_]+)`?", line.strip())
        if match:
            return match.group(1).lower()
    return "unknown"


def parse_candidate_rows(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    headers, table_rows = parse_path(path)
    return [[row.get(header, "") for header in headers] for row in table_rows if len(headers) >= 14]


def score_value(value: str) -> int:
    match = re.search(r"\d+", value or "")
    return int(match.group(0)) if match else 0


def compact_value(value: str, limit: int = 90) -> str:
    value = clean_cell(value)
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def counter_text(counter: Counter[str]) -> str:
    if not counter:
        return "none"
    return ", ".join(f"{key}: {value}" for key, value in sorted(counter.items()))


def render_result_highlights(candidate_table: Path, limit: int = 5) -> list[str]:
    rows = parse_candidate_rows(candidate_table)
    if not rows:
        return [
            "## Result Highlights",
            "",
            "- Candidate rows: 0",
            "- Channel distribution: none",
            "- Confidence distribution: none",
            "",
        ]

    channel_counts = Counter(row[4] for row in rows if len(row) > 4 and row[4])
    confidence_counts = Counter(row[6] for row in rows if len(row) > 6 and row[6])
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    ranked = sorted(
        rows,
        key=lambda row: (
            -confidence_rank.get((row[6] if len(row) > 6 else "").lower(), 0),
            -score_value(row[8] if len(row) > 8 else ""),
            (row[0] if row else "").lower(),
        ),
    )

    lines = [
        "## Result Highlights",
        "",
        f"- Candidate rows: {len(rows)}",
        f"- Channel distribution: {counter_text(channel_counts)}",
        f"- Confidence distribution: {counter_text(confidence_counts)}",
        "",
        f"Top {min(limit, len(ranked))} leads to review first:",
        "",
        "| Name | Email | Channel | Confidence | Score | Basic info | Source |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in ranked[:limit]:
        name = compact_value(row[0] if len(row) > 0 else "")
        email = compact_value(row[3] if len(row) > 3 else "")
        channel = compact_value(row[4] if len(row) > 4 else "")
        confidence = compact_value(row[6] if len(row) > 6 else "")
        score = compact_value(row[8] if len(row) > 8 else "")
        basic = compact_value(row[1] if len(row) > 1 else "", limit=80)
        source = compact_value(row[9] if len(row) > 9 else "", limit=80)
        lines.append(f"| {name} | {email} | {channel} | {confidence} | {score} | {basic} | {source} |")
    lines.append("")
    return lines


def unique_email_count(rows: list[list[str]]) -> int:
    found: set[str] = set()
    for row in rows:
        if len(row) <= 3:
            continue
        for email in emails_from(row[3]):
            found.add(email.lower())
    return len(found)


def render_handoff_readme(paths: dict[str, Path], args: argparse.Namespace) -> str:
    rows = parse_candidate_rows(paths["merged_table"])
    channel_counts = Counter(row[4] for row in rows if len(row) > 4 and row[4])
    confidence_counts = Counter(row[6] for row in rows if len(row) > 6 and row[6])
    lines = [
        "# Candidate Sourcing Handoff",
        "",
        f"- Final Markdown table: `{paths['merged_table']}`",
        f"- Final CSV table: `{paths['merged_csv']}`",
        f"- Canonical JSONL table: `{paths['merged_jsonl']}`",
        f"- Validation report: `{paths['validation_report']}`",
        f"- Validation status: `{report_status(paths['validation_report'])}`",
        f"- Candidate rows: {len(rows)}",
        f"- Unique public emails: {unique_email_count(rows)}",
        f"- Channel distribution: {counter_text(channel_counts)}",
        f"- Confidence distribution: {counter_text(confidence_counts)}",
        f"- Coverage preset: `{args.coverage_preset}`",
        f"- Focus: `{args.focus}`",
        f"- Require China public signal: `{args.require_china_signal}` (public evidence only; no nationality/ethnicity inference)",
        f"- Outreach generated: {'no' if args.no_outreach else 'yes, review-only until explicitly approved'}",
        "",
        "Boundary: the table only keeps emails found on public pages, public GitHub metadata, authorized platform imports, or linked public profile pages. It does not guess emails, log in to recruiting platforms, bypass anti-scraping controls, or send outreach.",
        "",
        "Next review step: manually verify email ownership, current affiliation, hiring intent, location, compensation range, and whether the contact should be approached before approving any outreach queue.",
        "",
    ]
    return "\n".join(lines)


def write_summary_and_readme(
    paths: dict[str, Path],
    platform_tables: list[Path],
    args: argparse.Namespace,
    notes: list[str] | None = None,
) -> None:
    paths["summary"].write_text(render_summary(paths, platform_tables, args, notes=notes), encoding="utf-8")
    paths["readme"].write_text(render_handoff_readme(paths, args), encoding="utf-8")


def render_summary(
    paths: dict[str, Path],
    platform_tables: list[Path],
    args: argparse.Namespace,
    notes: list[str] | None = None,
) -> str:
    if args.no_outreach:
        next_steps = [
            "1. Review the cleaned merged candidate table and remove weak/incorrect leads.",
            "2. Manually verify ownership, current affiliation, location, compensation range, and contact appropriateness.",
            "3. Rerun without `--no-outreach` only after the candidate table is approved for draft generation.",
        ]
    else:
        next_steps = [
            "1. Review the cleaned merged candidate table and remove weak/incorrect leads.",
            "2. Review the shortlist, outreach drafts, and queue CSV.",
            "3. Approve selected recipients with `approve_outreach_queue.py`.",
            "4. Dry-run `send_outreach.py` again before any real SMTP send.",
        ]

    lines = [
        "# Tech Candidate Sourcing Pipeline Summary",
        "",
        f"- JD: `{paths['jd']}`",
        f"- Coverage preset: `{args.coverage_preset}`",
        f"- Focus: `{args.focus}`",
        f"- Require China public signal: `{args.require_china_signal}` (public evidence only; no nationality/ethnicity inference)",
        f"- Depth: `{args.depth}`",
        f"- Public search table: `{paths['public_table']}`",
        f"- Channel queries: `{paths['queries']}`",
        f"- Paper-author follow-up queries: `{paths['paper_author_queries']}`",
        f"- Paper-author resolved seed URLs: `{paths['paper_author_seed_file']}`",
        f"- Paper-author candidate table: `{paths['paper_author_candidates']}`",
        f"- Publication-PDF email candidate table: `{paths['publication_email_candidates']}`",
        f"- Raw merged candidate table: `{paths['raw_merged_table']}`",
        f"- Cleaned base candidate table: `{paths['cleaned_base_table']}`",
        f"- Research-evidence enriched candidate table: `{paths['merged_table']}`",
        f"- Cleaned merged candidate CSV: `{paths['merged_csv']}`",
        f"- Canonical merged candidate JSONL: `{paths['merged_jsonl']}`",
        f"- Candidate validation report: `{paths['validation_report']}`",
        f"- Candidate validation status: `{report_status(paths['validation_report'])}`",
        f"- Outreach drafts: `{paths['drafts']}`",
        f"- Review queue CSV: `{paths['queue']}`",
        f"- Outreach queue validation report: `{paths['queue_validation_report']}`",
        f"- Outreach queue validation status: `{report_status(paths['queue_validation_report'])}`",
        f"- Review shortlist: `{paths['shortlist']}`",
        f"- Suggested allowlist: `{paths['allowlist']}`",
        f"- Dry-run send log: `{paths['dry_run_log']}`",
        f"- Platform import tables: {len(platform_tables)}",
        f"- Suppression lists: {len(args.suppression_list)}",
        "",
        "Next steps:",
        "",
        *next_steps,
        "",
        "No email is sent by this pipeline.",
        "",
    ]
    lines.extend(render_result_highlights(paths["merged_table"]))
    if notes:
        lines.extend(["## Notes", ""])
        lines.extend(f"- {note}" for note in notes)
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run sourcing, query expansion, merging, outreach drafts, queue, and dry-run checks.")
    parser.add_argument("--jd", default=None, type=Path, help="JD markdown/text file.")
    parser.add_argument("--jd-text", default="", help="Raw JD text. The pipeline saves it into the output directory.")
    parser.add_argument("--out-dir", default=None, type=Path, help="Output directory. Defaults to outputs/<jd-stem>_<timestamp>.")
    parser.add_argument("--prefix", default="", help="Output filename prefix. Defaults to JD file stem.")
    parser.add_argument(
        "--coverage-preset",
        choices=["custom", "broad"],
        default="custom",
        help="Use broad to expand deep academic, paper-author, GitHub user/contributor, and URL coverage.",
    )
    parser.add_argument(
        "--focus",
        choices=["general", "china"],
        default="general",
        help="Use china to prioritize China-affiliated or Chinese-language public sources without inferring protected identity.",
    )
    parser.add_argument("--require-china-signal", action="store_true", help="Keep only candidates with explicit public China-related evidence; never infer nationality or ethnicity.")
    parser.add_argument("--depth", choices=["quick", "deep"], default="quick")
    parser.add_argument("--search-limit", type=int, default=2)
    parser.add_argument("--max-urls", type=int, default=8)
    parser.add_argument("--github-user-limit", type=int, default=8)
    parser.add_argument("--github-contributor-limit", type=int, default=20)
    parser.add_argument("--github-commit-limit", type=int, default=20)
    parser.add_argument("--academic-link-limit", type=int, default=30)
    parser.add_argument("--paper-max-papers", type=int, default=5)
    parser.add_argument("--paper-max-conference-papers", type=int, default=8)
    parser.add_argument("--paper-max-openalex-papers", type=int, default=15)
    parser.add_argument("--dblp-author-profile-limit", type=int, default=15)
    parser.add_argument("--publication-pdf-limit", type=int, default=12)
    parser.add_argument("--publication-pdf-workers", type=int, default=3)
    parser.add_argument("--publication-pdf-max-pages", type=int, default=2)
    parser.add_argument("--paper-max-authors", type=int, default=12)
    parser.add_argument("--paper-seed-limit", type=int, default=12)
    parser.add_argument("--paper-seeds-per-query", type=int, default=2)
    parser.add_argument("--no-paper-author-resolve", action="store_true")
    parser.add_argument("--skip-publication-pdfs", action="store_true", help="Skip PDF email extraction while preserving the rest of the pipeline.")
    parser.add_argument("--no-arxiv", action="store_true", help="Skip arXiv metadata lookup.")
    parser.add_argument("--no-dblp", action="store_true", help="Skip DBLP metadata lookup.")
    parser.add_argument("--no-openalex", action="store_true", help="Skip OpenAlex metadata lookup.")
    parser.add_argument("--step-timeout", type=int, default=240, help="Timeout in seconds for each pipeline subprocess.")
    parser.add_argument("--seed-url", action="append", default=[])
    parser.add_argument("--seed-file", action="append", default=[], type=Path)
    parser.add_argument("--no-search", action="store_true")
    parser.add_argument("--no-github-search", action="store_true")
    parser.add_argument("--no-academic-expand", action="store_true")
    parser.add_argument("--collect-phones", action="store_true", help="Collect public phone-like strings. Disabled by default.")
    parser.add_argument("--platform-input", action="append", default=[], help="Optional platform=path CSV/TSV/text import. Repeatable.")
    parser.add_argument("--platform-source", default="authorized platform export", help="Source note for platform imports.")
    parser.add_argument("--location", default="", help="Optional location hint for channel queries.")
    parser.add_argument("--draft-limit", type=int, default=20)
    parser.add_argument("--shortlist-limit", type=int, default=10)
    parser.add_argument("--shortlist-min-score", type=int, default=70)
    parser.add_argument("--shortlist-min-confidence", choices=["low", "medium", "high"], default="medium")
    parser.add_argument("--shortlist-include-low", action="store_true")
    parser.add_argument("--sender-name", default="", help="Required unless --no-outreach is used.")
    parser.add_argument("--company-hint", default="", help="Required unless --no-outreach is used.")
    parser.add_argument("--outreach-language", choices=["auto", "zh", "en"], default="auto")
    parser.add_argument("--suppression-list", action="append", default=[], type=Path, help="Optional no-contact list for validation and dry-run sending. Repeatable.")
    parser.add_argument("--no-outreach", action="store_true", help="Stop after merged candidate table, CSV, validation report, and summary. Do not create drafts, queue, shortlist, or dry-run send log.")
    parser.add_argument("--allow-failed-candidate-table", action="store_true", help="Continue to outreach draft generation even if candidate table validation fails.")
    parser.add_argument("--allow-failed-outreach-queue", action="store_true", help="Continue to dry-run sending even if outreach queue validation fails.")
    args = parser.parse_args()

    if not args.jd and not args.jd_text:
        raise ValueError("Pass either --jd <file> or --jd-text <text>.")
    if args.jd and args.jd_text:
        raise ValueError("Pass only one of --jd or --jd-text; refusing to overwrite a supplied JD file.")
    if args.jd and not args.jd.exists():
        raise FileNotFoundError(args.jd)
    if not args.no_outreach and (not args.sender_name.strip() or not args.company_hint.strip()):
        raise ValueError("Outreach generation requires explicit --sender-name and --company-hint. Use --no-outreach for candidate-only runs.")
    apply_coverage_preset(args)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = safe_prefix(args.prefix or (args.jd.stem if args.jd else "pasted_jd"))
    out_dir = args.out_dir or Path("outputs") / f"{prefix}_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    scripts = script_dir()
    jd_path = args.jd if args.jd else out_dir / f"{prefix}_jd.md"
    if args.jd_text:
        jd_path.write_text(args.jd_text, encoding="utf-8")

    paths = {
        "jd": jd_path,
        "public_table": out_dir / f"{prefix}_public_candidates.md",
        "queries": out_dir / f"{prefix}_channel_queries.md",
        "paper_author_queries": out_dir / f"{prefix}_paper_author_queries.md",
        "paper_author_seed_file": out_dir / f"{prefix}_paper_author_seed_urls.txt",
        "paper_author_candidates": out_dir / f"{prefix}_paper_author_candidates.md",
        "publication_email_candidates": out_dir / f"{prefix}_publication_email_candidates.md",
        "raw_merged_table": out_dir / f"{prefix}_merged_candidates_raw.md",
        "cleaned_base_table": out_dir / f"{prefix}_merged_candidates_cleaned_base.md",
        "merged_table": out_dir / f"{prefix}_merged_candidates.md",
        "merged_csv": out_dir / f"{prefix}_merged_candidates.csv",
        "merged_jsonl": out_dir / f"{prefix}_merged_candidates.jsonl",
        "validation_report": out_dir / f"{prefix}_candidate_validation_report.md",
        "drafts": out_dir / f"{prefix}_outreach_drafts.md",
        "queue": out_dir / f"{prefix}_outreach_queue.csv",
        "queue_validation_report": out_dir / f"{prefix}_outreach_queue_validation_report.md",
        "shortlist": out_dir / f"{prefix}_review_shortlist.md",
        "allowlist": out_dir / f"{prefix}_suggested_allowlist.txt",
        "dry_run_log": out_dir / f"{prefix}_send_dry_run_log.csv",
        "summary": out_dir / f"{prefix}_pipeline_summary.md",
        "readme": out_dir / "README.md",
    }
    platform_tables: list[Path] = []
    global _ACTIVE_CONTEXT
    _ACTIVE_CONTEXT = (paths, platform_tables, args)

    run_step(build_source_command(args, jd_path, paths["public_table"]), args.step_timeout)
    query_command = [
        sys.executable,
        str(scripts / "generate_channel_queries.py"),
        "--jd",
        str(jd_path),
        "--out",
        str(paths["queries"]),
    ]
    if args.location:
        query_command.extend(["--location", args.location])
    run_step(query_command, args.step_timeout)

    paper_query_command = [
            sys.executable,
            str(scripts / "generate_paper_author_queries.py"),
            "--jd",
            str(jd_path),
            "--out",
            str(paths["paper_author_queries"]),
            "--max-papers",
            str(args.paper_max_papers),
            "--max-conference-papers",
            str(args.paper_max_conference_papers),
            "--max-openalex-papers",
            str(args.paper_max_openalex_papers),
            "--max-authors",
            str(args.paper_max_authors),
            "--dblp-author-profile-limit",
            str(args.dblp_author_profile_limit),
            "--deadline-seconds",
            str(max(1, args.step_timeout - 60)),
        ]
    if args.no_arxiv:
        paper_query_command.append("--no-arxiv")
    if args.no_dblp:
        paper_query_command.append("--no-dblp")
    if args.no_openalex:
        paper_query_command.append("--no-openalex")
    run_step(paper_query_command, args.step_timeout)

    publication_command = [
            sys.executable,
            str(scripts / "source_publication_emails.py"),
            "--queries",
            str(paths["paper_author_queries"]),
            "--out",
            str(paths["publication_email_candidates"]),
            "--max-papers",
            str(args.publication_pdf_limit),
            "--workers",
            str(args.publication_pdf_workers),
            "--max-pages",
            str(args.publication_pdf_max_pages),
            "--deadline-seconds",
            str(max(1, args.step_timeout - 60)),
        ]
    if args.skip_publication_pdfs:
        publication_command.append("--skip")
    run_step(publication_command, args.step_timeout)

    paper_author_table_created = False
    if not args.no_paper_author_resolve:
        run_step(
            [
                sys.executable,
                str(scripts / "resolve_paper_author_seeds.py"),
                "--queries",
                str(paths["paper_author_queries"]),
                "--out",
                str(paths["paper_author_seed_file"]),
                "--per-query",
                str(args.paper_seeds_per_query),
                "--max-urls",
                str(args.paper_seed_limit),
                "--max-authors",
                str(args.paper_max_authors),
                "--deadline-seconds",
                str(max(1, args.step_timeout - 60)),
            ],
            args.step_timeout,
        )
        paper_source_command = [
                sys.executable,
                str(scripts / "source_candidates.py"),
                "--jd",
                str(jd_path),
                "--out",
                str(paths["paper_author_candidates"]),
                "--depth",
                args.depth,
                "--max-urls",
                str(args.paper_seed_limit),
                "--academic-link-limit",
                str(args.academic_link_limit),
                "--focus",
                args.focus,
                "--github-contributor-limit",
                str(args.github_contributor_limit),
                "--github-commit-limit",
                str(args.github_commit_limit),
                "--seed-file",
                str(paths["paper_author_seed_file"]),
                "--no-search",
                "--no-github-search",
                "--deadline-seconds",
                str(max(1, args.step_timeout - 60)),
            ]
        if args.require_china_signal:
            paper_source_command.append("--require-china-signal")
        if args.collect_phones:
            paper_source_command.append("--collect-phones")
        run_step(paper_source_command, args.step_timeout)
        paper_author_table_created = True

    merge_inputs = [paths["public_table"], paths["publication_email_candidates"]]
    if paper_author_table_created:
        merge_inputs.append(paths["paper_author_candidates"])
    for idx, spec in enumerate(args.platform_input, start=1):
        platform, input_path = platform_spec(spec)
        table_path = out_dir / f"{prefix}_{idx}_{platform}_candidates.md"
        run_step(
            [
                sys.executable,
                str(scripts / "import_platform_candidates.py"),
                "--input",
                str(input_path),
                "--out",
                str(table_path),
                "--platform",
                platform,
                "--source",
                args.platform_source,
            ],
            args.step_timeout,
        )
        platform_tables.append(table_path)
        merge_inputs.append(table_path)

    merge_command = [sys.executable, str(scripts / "merge_candidate_tables.py"), "--out", str(paths["raw_merged_table"])]
    for table in merge_inputs:
        merge_command.extend(["--input", str(table)])
    run_step(merge_command, args.step_timeout)

    filter_command = [
            sys.executable,
            str(scripts / "filter_candidate_table.py"),
            "--input",
            str(paths["raw_merged_table"]),
            "--out",
            str(paths["cleaned_base_table"]),
        ]
    if args.require_china_signal:
        filter_command.append("--require-china-signal")
    run_step(filter_command, args.step_timeout)

    run_step(
        [
            sys.executable,
            str(scripts / "enrich_candidate_research.py"),
            "--input",
            str(paths["cleaned_base_table"]),
            "--out",
            str(paths["merged_table"]),
            "--publication-queries",
            str(paths["paper_author_queries"]),
        ],
        args.step_timeout,
    )

    run_step(
        [
            sys.executable,
            str(scripts / "export_candidates_csv.py"),
            "--candidates",
            str(paths["merged_table"]),
            "--out",
            str(paths["merged_csv"]),
            "--jsonl-out",
            str(paths["merged_jsonl"]),
        ],
        args.step_timeout,
    )

    run_step(
        [
            sys.executable,
            str(scripts / "validate_candidate_table.py"),
            "--candidates",
            str(paths["merged_table"]),
            "--out",
            str(paths["validation_report"]),
        ],
        args.step_timeout,
    )

    candidate_status = report_status(paths["validation_report"])
    if candidate_status == "fail" and not args.allow_failed_candidate_table:
        write_summary_and_readme(
            paths,
            platform_tables,
            args,
            notes=[
                "Stopped before outreach drafts because candidate table validation failed.",
                "Fix the candidate table or rerun with --allow-failed-candidate-table for debugging only.",
            ],
        )
        print(f"Candidate validation failed; stopped before outreach drafts. Summary: {paths['summary']}")
        return 2

    if args.no_outreach:
        write_summary_and_readme(
            paths,
            platform_tables,
            args,
            notes=[
                "Candidate-only run: stopped after merged candidate table, CSV export, and candidate validation.",
                "Outreach drafts, queue, shortlist, allowlist, and dry-run send log were intentionally not generated.",
            ],
        )
        print(f"Candidate-only run complete. Summary: {paths['summary']}")
        return 0

    shortlist_command = [
        sys.executable,
        str(scripts / "shortlist_candidates.py"),
        "--candidates",
        str(paths["merged_table"]),
        "--out",
        str(paths["shortlist"]),
        "--allowlist-out",
        str(paths["allowlist"]),
        "--limit",
        str(args.shortlist_limit),
        "--min-score",
        str(args.shortlist_min_score),
        "--min-confidence",
        args.shortlist_min_confidence,
        "--focus",
        args.focus,
    ]
    if args.shortlist_include_low:
        shortlist_command.append("--include-low")
    if args.require_china_signal:
        shortlist_command.append("--require-china-signal")
    run_step(shortlist_command, args.step_timeout)

    run_step(
        [
            sys.executable,
            str(scripts / "draft_outreach.py"),
            "--candidates",
            str(paths["merged_table"]),
            "--jd",
            str(jd_path),
            "--out",
            str(paths["drafts"]),
            "--queue-csv",
            str(paths["queue"]),
            "--allowlist",
            str(paths["allowlist"]),
            "--limit",
            str(args.draft_limit),
            "--sender-name",
            args.sender_name,
            "--company-hint",
            args.company_hint,
            "--language",
            args.outreach_language,
        ],
        args.step_timeout,
    )

    queue_validation_command = [
        sys.executable,
        str(scripts / "validate_outreach_queue.py"),
        "--queue",
        str(paths["queue"]),
        "--out",
        str(paths["queue_validation_report"]),
    ]
    add_suppression_args(queue_validation_command, args)
    run_step(queue_validation_command, args.step_timeout)
    queue_status = report_status(paths["queue_validation_report"])
    if queue_status == "fail" and not args.allow_failed_outreach_queue:
        write_summary_and_readme(
            paths,
            platform_tables,
            args,
            notes=[
                "Stopped before dry-run sending because outreach queue validation failed.",
                "Fix the queue or rerun with --allow-failed-outreach-queue for debugging only.",
            ],
        )
        print(f"Outreach queue validation failed; stopped before dry-run. Summary: {paths['summary']}")
        return 3

    if paths["dry_run_log"].exists():
        paths["dry_run_log"].unlink()
    dry_run_command = [
        sys.executable,
        str(scripts / "send_outreach.py"),
        "--queue",
        str(paths["queue"]),
        "--log",
        str(paths["dry_run_log"]),
    ]
    add_suppression_args(dry_run_command, args)
    run_step(dry_run_command, args.step_timeout)

    write_summary_and_readme(paths, platform_tables, args)
    print(f"Wrote pipeline summary to {paths['summary']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        if _ACTIVE_CONTEXT is not None:
            active_paths, active_platform_tables, active_args = _ACTIVE_CONTEXT
            try:
                write_summary_and_readme(
                    active_paths,
                    active_platform_tables,
                    active_args,
                    notes=[
                        f"Pipeline failed: {type(exc).__name__}: {exc}",
                        "Completed artifacts were preserved for debugging or resume.",
                    ],
                )
                print(f"Pipeline failed; summary preserved at {active_paths['summary']}", file=sys.stderr)
            except Exception as summary_exc:
                print(f"Pipeline failed and summary writing also failed: {summary_exc}", file=sys.stderr)
        raise
