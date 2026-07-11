#!/usr/bin/env python3
"""Extract uniquely attributable public author emails from publication PDFs."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

try:
    import fitz
except ImportError:  # Optional at runtime; the pipeline can continue without PDF extraction.
    fitz = None
try:
    from pypinyin import lazy_pinyin
except ImportError:
    lazy_pinyin = None

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from safe_http import safe_get_public
from md_table import parse_path, render_rows
from candidate_schema import EMAIL_RE, STANDARD_HEADERS
from china_signals import china_public_evidence


GROUPED_EMAIL_RE = re.compile(r"\{([^{}]{1,180})\}\s*@\s*([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
BLOCKED_EMAIL_PARTS = {
    "noreply", "example", "yourname", "sentry", "googlegroups", "publisher", "editor",
    "editorial", "mailing", "webmaster", "support", "contact", "copyright", "permissions",
}
PUBLICATION_NOTES: list[str] = []


def load_source_module():
    script_path = Path(__file__).with_name("source_candidates.py")
    spec = importlib.util.spec_from_file_location("source_candidates", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load source_candidates.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["source_candidates"] = module
    spec.loader.exec_module(module)
    return module


def parse_rows(path: Path) -> list[dict[str, str]]:
    _headers, rows = parse_path(path)
    return rows


def paper_url(entry: str) -> str:
    match = re.search(r"\[paper\]\(([^)]+)\)", entry or "")
    return match.group(1).strip() if match else ""


def pdf_url(url: str) -> str:
    if not url:
        return ""
    url = url.replace("http://arxiv.org/", "https://arxiv.org/")
    parsed = urlparse(url)
    if "arxiv.org" in parsed.netloc and "/abs/" in parsed.path:
        identifier = parsed.path.split("/abs/", 1)[1]
        return f"https://arxiv.org/pdf/{identifier}.pdf"
    if "arxiv.org" in parsed.netloc and "/pdf/" in parsed.path:
        return url if parsed.path.lower().endswith(".pdf") else url + ".pdf"
    if "doi.org" in parsed.netloc and "/10.18653/v1/" in parsed.path.lower():
        identifier = parsed.path.split("/10.18653/v1/", 1)[1]
        return f"https://aclanthology.org/{identifier}.pdf"
    if "aclanthology.org" in parsed.netloc and not parsed.path.endswith(".pdf"):
        return url.rstrip("/") + ".pdf"
    if "openreview.net" in parsed.netloc and "/forum" in parsed.path and parsed.query:
        return url.replace("/forum", "/pdf")
    if parsed.path.lower().endswith(".pdf"):
        return url
    return ""


def publication_groups(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        url = paper_url(row.get("Entry", ""))
        explicit_pdf = row.get("PDF URL", "").strip()
        if not url and not explicit_pdf:
            continue
        key = url or explicit_pdf
        item = grouped.setdefault(
            key,
            {
                "paper_url": url or explicit_pdf,
                "pdf_url": explicit_pdf or pdf_url(url),
                "title": row.get("Paper", ""),
                "venue": row.get("Venue", ""),
                "source": row.get("Discovery source", ""),
                "topics": row.get("Research topics", ""),
                "authors": {},
            },
        )
        authors = item["authors"]
        if isinstance(authors, dict):
            authors[row.get("Author", "")] = row.get("Public affiliation", "")
    return [item for item in grouped.values() if item.get("pdf_url")]


def extract_emails(text: str) -> set[str]:
    found = {email.lower().strip(".,;:") for email in EMAIL_RE.findall(text or "")}
    for locals_text, domain in GROUPED_EMAIL_RE.findall(text or ""):
        for local in re.split(r"[,;\s]+", locals_text):
            local = local.strip(" .,+")
            if re.fullmatch(r"[A-Za-z0-9._+-]{2,}", local):
                found.add(f"{local}@{domain}".lower())
    return {
        email
        for email in found
        if not any(fragment in email for fragment in BLOCKED_EMAIL_PARTS)
        and not email.endswith("@arxiv.org")
    }


def pdf_first_pages(url: str, timeout: int, max_pages: int = 2) -> str:
    if fitz is None:
        raise RuntimeError("PyMuPDF is not installed; publication PDF extraction was skipped")
    response = safe_get_public(
        url,
        headers={"User-Agent": "tech-candidate-sourcing/0.3"},
        timeout=timeout,
        max_bytes=25 * 1024 * 1024,
    )
    document = fitz.open(stream=io.BytesIO(response.content), filetype="pdf")
    texts = [document.load_page(index).get_text("text") for index in range(min(max_pages, document.page_count))]
    document.close()
    return "\n".join(texts)


def author_token_variants(name: str) -> list[list[str]]:
    ascii_tokens = re.findall(r"[a-z]{2,}", name.lower())
    variants: list[list[str]] = [ascii_tokens] if ascii_tokens else []
    if re.search(r"[\u4e00-\u9fff]", name) and lazy_pinyin is not None:
        pinyin_tokens = [re.sub(r"[^a-z]", "", part.lower()) for part in lazy_pinyin(name)]
        pinyin_tokens = [part for part in pinyin_tokens if part]
        if pinyin_tokens:
            variants.append(pinyin_tokens)
    return variants


def identity_score(local: str, tokens: list[str]) -> int:
    if not tokens:
        return 0
    compact = re.sub(r"[^a-z0-9]", "", local.lower())
    full = "".join(tokens)
    reverse = tokens[-1] + "".join(tokens[:-1]) if len(tokens) >= 2 else full
    exact_candidates = {full, reverse}
    if any(re.fullmatch(re.escape(candidate) + r"\d*", compact) for candidate in exact_candidates if len(candidate) >= 4):
        return 12
    if len(tokens) >= 2:
        initial_candidates = {tokens[0][0] + tokens[-1], tokens[-1] + tokens[0][0]}
        if any(re.fullmatch(re.escape(candidate) + r"\d*", compact) for candidate in initial_candidates if len(candidate) >= 4):
            return 9
        initials = "".join(token[0] for token in tokens)
        if len(initials) >= 2 and re.fullmatch(re.escape(initials) + r"\d*", compact):
            return 8
    if len(tokens) == 1 and len(tokens[0]) >= 5 and re.fullmatch(re.escape(tokens[0]) + r"\d*", compact):
        return 8
    return 0


def unique_author_for_email(email: str, authors: dict[str, str], source=None) -> str:
    local = email.split("@", 1)[0].lower()
    scored: list[tuple[int, str]] = []
    for name in authors:
        if not name:
            continue
        score = max((identity_score(local, tokens) for tokens in author_token_variants(name)), default=0)
        scored.append((score, name))
    if not scored:
        return ""
    scored.sort(reverse=True)
    best_score, best_name = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else -1
    if best_score >= 8 and best_score > second_score:
        return best_name
    return ""


def china_signal(affiliation: str, email: str) -> str:
    evidence = china_public_evidence(f"{affiliation} {email}")
    if evidence:
        return "；".join(evidence[:4])
    return "未发现明确的中国高校/机构/中文公开来源信号"


def infer_topics(title: str, provided: str) -> str:
    if provided and provided not in {"论文主题需复核", "公开页关键词不足"}:
        return provided
    lowered = title.lower()
    mapping = [
        (("multi-agent", "multi agent"), "multi-agent"),
        (("agent", "agentic"), "agent"),
        (("reasoning",), "reasoning"),
        (("tool", "mcp"), "tool use"),
        (("planning", "search"), "planning"),
        (("memory",), "memory"),
        (("evaluation", "benchmark"), "evaluation"),
        (("code", "program"), "code agent"),
        (("scientist", "research"), "ai scientist"),
    ]
    topics = [label for tokens, label in mapping if any(token in lowered for token in tokens)]
    return "; ".join(dict.fromkeys(topics)) or "论文主题需复核"


def source_candidates(
    groups: list[dict[str, object]],
    limit: int,
    timeout: int,
    workers: int = 3,
    max_pages: int = 2,
    deadline_at: float = 0.0,
) -> list[dict[str, str]]:
    source = load_source_module()
    candidates: list[dict[str, str]] = []
    seen_emails: set[str] = set()
    skipped_unattributed = 0
    failed_pdfs = 0

    def fetch_group(group: dict[str, object]) -> tuple[dict[str, object], str]:
        if deadline_at and time.monotonic() >= deadline_at:
            return group, ""
        try:
            return group, pdf_first_pages(str(group["pdf_url"]), timeout, max_pages=max_pages)
        except Exception as exc:
            nonlocal failed_pdfs
            failed_pdfs += 1
            if len(PUBLICATION_NOTES) < 10:
                PUBLICATION_NOTES.append(f"PDF fetch/extract failed for {group.get('pdf_url')}: {type(exc).__name__}: {exc}")
            return group, ""

    selected = groups[:limit]
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 6))) as executor:
        fetched = executor.map(fetch_group, selected)
        for group, text in fetched:
            if deadline_at and time.monotonic() >= deadline_at:
                PUBLICATION_NOTES.append("Internal deadline reached during PDF extraction; keeping partial candidates.")
                break
            if not text:
                continue
            authors = group.get("authors") or {}
            if not isinstance(authors, dict):
                continue
            emails = extract_emails(text) | source.extract_emails(text)
            for email in sorted(emails):
                email = email.lower()
                if email in seen_emails:
                    continue
                name = unique_author_for_email(email, authors, source)
                if not name:
                    skipped_unattributed += 1
                    continue
                seen_emails.add(email)
                affiliation = str(authors.get(name) or "公开论文作者；机构需复核")
                title = str(group.get("title") or "公开论文")
                topics = infer_topics(title, str(group.get("topics") or ""))
                venue = str(group.get("venue") or group.get("source") or "publication")
                topic_count = len([part for part in re.split(r"[;,；]", topics) if part.strip()])
                fit_score = min(78, 64 + min(topic_count, 7) * 2)
                recommendation = "可聊" if fit_score >= 70 else "备选"
                candidates.append(
                    {
                        "姓名": name,
                        "基础信息": affiliation,
                        "电话": "暂无",
                        "邮箱": email,
                        "渠道": "publication_pdf",
                        "线索类型": "publication_author_email",
                        "置信度": "medium",
                        "推荐级别": recommendation,
                        "匹配分": f"{fit_score}/100",
                        "来源": str(group["pdf_url"]),
                        "命中关键词": topics,
                        "推荐点": f"公开论文 PDF 显示作者邮箱，论文主题与 JD 相关：{title}",
                        "风险点/待确认": "邮箱与作者姓名已做唯一匹配；仍需人工确认当前机构、本人 ownership、求职意向、地点和薪资",
                        "建议动作": "核验作者主页和当前 affiliation 后优先个性化触达",
                        "研究方向": topics,
                        "代表作/项目证据": f"{title} ({venue}) {group['paper_url']}",
                        "中国相关公开信号": china_signal(affiliation, email),
                    }
                )
    PUBLICATION_NOTES.append(f"PDFs failed/skipped: {failed_pdfs}; emails skipped as ambiguous/unattributed: {skipped_unattributed}")
    return candidates


def render(rows: list[dict[str, str]], query_path: Path) -> str:
    lines = [
        "# 公开论文作者邮箱候选人表",
        "",
        f"- Publication query table: `{query_path}`",
        f"- Uniquely attributed public author emails: {len(rows)}",
        "- 仅保留公开论文 PDF 中出现且能与一位作者姓名唯一匹配的邮箱；不猜邮箱。",
        f"- Diagnostics: {'; '.join(PUBLICATION_NOTES) if PUBLICATION_NOTES else 'none'}",
        "",
    ]
    return "\n".join(lines) + "\n" + render_rows(STANDARD_HEADERS, rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract uniquely attributable public author emails from publication PDFs.")
    parser.add_argument("--queries", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--max-papers", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--skip", action="store_true", help="Write an empty publication candidate table without importing/fetching PDFs.")
    parser.add_argument("--deadline-seconds", type=int, default=0)
    args = parser.parse_args()

    groups = publication_groups(parse_rows(args.queries))
    if args.skip:
        PUBLICATION_NOTES.append("Publication PDF extraction explicitly skipped.")
        candidates = []
    else:
        deadline_at = time.monotonic() + args.deadline_seconds if args.deadline_seconds > 0 else 0.0
        candidates = source_candidates(groups, args.max_papers, args.timeout, workers=args.workers, max_pages=args.max_pages, deadline_at=deadline_at)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(candidates, args.queries), encoding="utf-8")
    print(f"Wrote {len(candidates)} publication-email candidates from {min(len(groups), args.max_papers)} PDFs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
