#!/usr/bin/env python3
"""Resolve paper-author follow-up queries into public seed URLs.

Input is the Markdown table produced by generate_paper_author_queries.py.
Output is a text seed file that can be passed to source_candidates.py.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from md_table import parse_path


def load_source_module():
    script_path = Path(__file__).with_name("source_candidates.py")
    spec = importlib.util.spec_from_file_location("source_candidates", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load source_candidates.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["source_candidates"] = module
    spec.loader.exec_module(module)
    return module


def paper_author_queries(path: Path, include_scholar: bool = False) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen_authors: set[str] = set()
    _headers, table_rows = parse_path(path)
    for item in table_rows:
        channel = item.get("Channel", "").strip()
        if channel not in {"author_homepage", "academic_author"} and not (include_scholar and channel == "scholar_author"):
            continue
        author = item.get("Author", "").strip()
        author_key = re.sub(r"\W+", "", author.lower())
        if author_key in seen_authors:
            continue
        seen_authors.add(author_key)
        rows.append(
            {
                "author": author,
                "channel": channel,
                "query": item.get("Query", "").strip(),
                "direct_urls": item.get("Author URLs", "").replace("<br>", ";").strip(),
            }
        )
    return rows


def probable_seed_url(url: str, source) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if parsed.scheme not in {"http", "https"} or not host:
        return False
    blocked_hosts = [
        "arxiv.org",
        "bing.com",
        "duckduckgo.com",
        "facebook.com",
        "google.com",
        "linkedin.com",
        "openreview.net",
        "researchgate.net",
        "semanticscholar.org",
        "twitter.com",
        "wikimedia.org",
        "wikipedia.org",
        "x.com",
        "youtube.com",
        "zhihu.com",
    ]
    if any(blocked in host for blocked in blocked_hosts):
        return False
    if path.endswith((".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".zip")):
        return False
    if host.startswith(("support.", "help.")) or any(token in path for token in ["/contactus", "/contact-us", "/support", "/help"]):
        return False
    if source.is_non_candidate_page(url=url):
        return False
    return True


def relevant_to_author(url: str, author: str, source) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    url_text = f"{host}{parsed.path}".lower()
    if source.is_academic_host(host) or "github.io" in host or host == "github.com":
        return True
    compact_author = "".join(ch.lower() for ch in author if ch.isalnum())
    if compact_author and compact_author in "".join(ch for ch in url_text if ch.isalnum()):
        return True
    tokens = [token.lower() for token in author.replace(".", " ").split() if len(token) >= 5]
    return any(token in url_text for token in tokens)


def resolve_queries(
    queries: list[dict[str, str]],
    per_query: int,
    max_urls: int,
    delay: float,
    workers: int = 4,
    deadline_at: float = 0.0,
) -> list[dict[str, str]]:
    source = load_source_module()
    resolved: list[dict[str, str]] = []
    seen: set[str] = set()

    unresolved: list[dict[str, str]] = []
    for row in queries:
        if deadline_at and time.monotonic() >= deadline_at:
            break
        direct_urls = [value.strip() for value in row.get("direct_urls", "").split(";") if value.strip()]
        kept_direct = False
        for url in direct_urls:
            if len(resolved) >= max_urls:
                break
            if not probable_seed_url(url, source) or url in seen:
                continue
            seen.add(url)
            resolved.append(
                {
                    "url": url,
                    "author": row["author"],
                    "channel": "dblp_author_url",
                    "query": row["query"],
                }
            )
            kept_direct = True
        if not kept_direct:
            unresolved.append(row)

    if len(resolved) >= max_urls:
        return resolved

    def search_row(row: dict[str, str]) -> tuple[dict[str, str], list[str]]:
        if deadline_at and time.monotonic() >= deadline_at:
            return row, []
        urls = source.search_web(row["query"], limit=per_query * 3)
        if delay:
            time.sleep(delay)
        return row, urls

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as executor:
        searched = executor.map(search_row, unresolved)
        for row, urls in searched:
            if deadline_at and time.monotonic() >= deadline_at:
                break
            if len(resolved) >= max_urls:
                break
            kept_for_query = 0
            for url in urls:
                if not probable_seed_url(url, source):
                    continue
                if not relevant_to_author(url, row["author"], source):
                    continue
                if url in seen:
                    continue
                seen.add(url)
                resolved.append(
                    {
                        "url": url,
                        "author": row["author"],
                        "channel": row["channel"],
                        "query": row["query"],
                    }
                )
                kept_for_query += 1
                if len(resolved) >= max_urls or kept_for_query >= per_query:
                    break
    return resolved


def render_seed_file(rows: list[dict[str, str]], query_path: Path) -> str:
    lines = [
        "# Public seed URLs resolved from paper-author follow-up queries.",
        f"# Source query table: {query_path}",
        "# Feed this file into source_candidates.py with --seed-file.",
        "",
    ]
    for row in rows:
        lines.append(f"# author={row['author']} channel={row['channel']} query={row['query']}")
        lines.append(row["url"])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve paper-author queries into public seed URLs.")
    parser.add_argument("--queries", required=True, help="Paper-author query Markdown file.")
    parser.add_argument("--out", required=True, help="Output seed URL text file.")
    parser.add_argument("--per-query", type=int, default=2)
    parser.add_argument("--max-urls", type=int, default=12)
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-authors", type=int, default=40)
    parser.add_argument("--include-scholar", action="store_true")
    parser.add_argument("--deadline-seconds", type=int, default=0)
    args = parser.parse_args()

    query_path = Path(args.queries)
    rows = paper_author_queries(query_path, include_scholar=args.include_scholar)
    rows = rows[: max(1, args.max_authors)]
    deadline_at = time.monotonic() + args.deadline_seconds if args.deadline_seconds > 0 else 0.0
    resolved = resolve_queries(rows, per_query=args.per_query, max_urls=args.max_urls, delay=args.delay, workers=args.workers, deadline_at=deadline_at)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_seed_file(resolved, query_path), encoding="utf-8")
    print(f"Wrote {len(resolved)} paper-author seed URLs to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
