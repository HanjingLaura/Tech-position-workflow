#!/usr/bin/env python3
"""Generate public paper-author evidence and follow-up queries from a JD.

This script uses public arXiv, DBLP, and OpenAlex metadata to find likely
authors, then creates reviewable queries and PDF evidence for public-email
sourcing. It does not guess emails and does not contact authors.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote_plus

import requests


TOP_CONFERENCE_VENUES = {
    "neurips": "NeurIPS",
    "nips": "NeurIPS",
    "iclr": "ICLR",
    "icml": "ICML",
    "acl": "ACL",
    "emnlp": "EMNLP",
    "colm": "COLM",
    "aaai": "AAAI",
    "ijcai": "IJCAI",
}
OPENALEX_NOTES: list[str] = []
ACTIVE_DEADLINE = 0.0


def deadline_reached() -> bool:
    return bool(ACTIVE_DEADLINE and time.monotonic() >= ACTIVE_DEADLINE)


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from jd_utils import jd_keywords
from md_table import escape_md


def text_of(element: ET.Element | None) -> str:
    if element is None or element.text is None:
        return ""
    return " ".join(element.text.split())


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child_text(entry: ET.Element, name: str) -> str:
    for child in entry:
        if local_name(child.tag) == name:
            return text_of(child)
    return ""


def child_elements(entry: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in entry if local_name(child.tag) == name]


def arxiv_query_url(keywords: list[str], max_papers: int) -> str:
    query_terms = [kw for kw in keywords[:4] if kw]
    if not query_terms:
        query_terms = ["machine learning"]
    search_query = " OR ".join(f'all:"{term}"' for term in query_terms[:4])
    return (
        "https://export.arxiv.org/api/query?"
        f"search_query={quote_plus(search_query)}&start=0&max_results={max_papers}"
        "&sortBy=submittedDate&sortOrder=descending"
    )


def fetch_bytes(url: str, timeout: int, user_agent: str, attempts: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, headers={"User-Agent": user_agent}, timeout=timeout)
            if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < attempts:
                retry_after = response.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.isdigit() else 3.0 * (attempt + 1)
                time.sleep(min(delay, 12.0))
                continue
            response.raise_for_status()
            return response.content
        except Exception as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                raise
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"Request failed: {last_error}")


def fetch_arxiv_xml(keywords: list[str], max_papers: int, timeout: int) -> str:
    url = arxiv_query_url(keywords, max_papers)
    return fetch_bytes(url, timeout, "tech-candidate-sourcing/0.2").decode("utf-8", errors="replace")


def parse_arxiv_entries(xml_text: str) -> list[dict[str, object]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    entries: list[dict[str, object]] = []
    for entry in root.iter():
        if local_name(entry.tag) != "entry":
            continue
        authors = []
        for author in child_elements(entry, "author"):
            name = child_text(author, "name")
            if name and name not in authors:
                authors.append(name)
        entries.append(
            {
                "title": child_text(entry, "title"),
                "url": child_text(entry, "id"),
                "published": child_text(entry, "published")[:10],
                "authors": authors,
                "source": "arXiv API",
                "venue": "arXiv",
                "pdf_url": child_text(entry, "id").replace("/abs/", "/pdf/") + ".pdf",
            }
        )
    return entries


def openalex_search_phrases(keywords: list[str]) -> list[str]:
    useful = [keyword for keyword in keywords if keyword.lower() not in {"python", "pytorch"}]
    if not useful:
        useful = ["machine learning"]
    candidates = [
        useful[:4],
        useful[:2] + useful[4:7],
        useful[2:6],
        useful[:1] + useful[5:9],
    ]
    phrases: list[str] = []
    for terms in candidates:
        phrase = " ".join(dict.fromkeys(term for term in terms if term)).strip()
        if phrase and phrase not in phrases:
            phrases.append(phrase)
    return phrases


def fetch_openalex_payloads(keywords: list[str], max_papers: int, timeout: int) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    errors: list[Exception] = []
    per_page = max(10, min(max_papers, 50))
    for phrase in openalex_search_phrases(keywords):
        if deadline_reached():
            OPENALEX_NOTES.append("Internal deadline reached during OpenAlex lookup; keeping partial payloads.")
            break
        for attempt in range(3):
            try:
                response = requests.get(
                    "https://api.openalex.org/works",
                    params={
                        "search": phrase,
                        "filter": "from_publication_date:2022-01-01,has_fulltext:true",
                        "per-page": per_page,
                        "select": "id,display_name,publication_year,doi,authorships,primary_location,best_oa_location",
                    },
                    headers={"User-Agent": "tech-candidate-sourcing/0.3"},
                    timeout=timeout,
                )
                if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict):
                    payloads.append(payload)
                break
            except Exception as exc:
                errors.append(exc)
                if attempt < 2:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                OPENALEX_NOTES.append(f"OpenAlex phrase failed after retries: {phrase!r}: {type(exc).__name__}: {exc}")
        time.sleep(0.25)
    if not payloads and errors:
        raise errors[-1]
    return payloads


def openalex_location(work: dict[str, object], key: str) -> dict[str, object]:
    value = work.get(key)
    return value if isinstance(value, dict) else {}


def openalex_title_relevant(title: str, keywords: list[str]) -> bool:
    generic = {"llm", "nlp", "python", "pytorch", "machine learning", "ai"}
    signals = [keyword.lower() for keyword in keywords if keyword.lower() not in generic and len(keyword) >= 3]
    if not signals:
        signals = [keyword.lower() for keyword in keywords[:4]]
    lowered = title.lower()
    return not signals or any(signal in lowered for signal in signals)


def parse_openalex_entries(
    payloads: list[dict[str, object]],
    max_papers: int,
    keywords: list[str] | None = None,
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    seen_titles: set[str] = set()
    for payload in payloads:
        results = payload.get("results", []) if isinstance(payload, dict) else []
        for work in results if isinstance(results, list) else []:
            if not isinstance(work, dict):
                continue
            title = str(work.get("display_name") or "").strip()
            if not title or title.lower() in seen_titles or not openalex_title_relevant(title, keywords or []):
                continue
            best_oa = openalex_location(work, "best_oa_location")
            primary = openalex_location(work, "primary_location")
            pdf_link = str(best_oa.get("pdf_url") or primary.get("pdf_url") or "").strip()
            if not pdf_link:
                continue
            landing = str(
                work.get("doi")
                or best_oa.get("landing_page_url")
                or primary.get("landing_page_url")
                or work.get("id")
                or ""
            )
            source_data = primary.get("source") if isinstance(primary.get("source"), dict) else {}
            venue = str(source_data.get("display_name") or "OpenAlex open-access work")
            authors: list[dict[str, object]] = []
            for authorship in work.get("authorships") or []:
                if not isinstance(authorship, dict):
                    continue
                author_data = authorship.get("author") if isinstance(authorship.get("author"), dict) else {}
                name = str(author_data.get("display_name") or "").strip()
                if not name:
                    continue
                institutions: list[str] = []
                for institution in authorship.get("institutions") or []:
                    if not isinstance(institution, dict):
                        continue
                    institution_name = str(institution.get("display_name") or "").strip()
                    country_code = str(institution.get("country_code") or "").strip()
                    if institution_name:
                        institutions.append(f"{institution_name} ({country_code})" if country_code else institution_name)
                for raw_affiliation in authorship.get("raw_affiliation_strings") or []:
                    value = str(raw_affiliation or "").strip()
                    if value and value not in institutions:
                        institutions.append(value)
                authors.append(
                    {
                        "name": name,
                        "pid": str(author_data.get("id") or ""),
                        "affiliation": "; ".join(dict.fromkeys(institutions)),
                        "urls": [],
                    }
                )
            if not authors:
                continue
            entries.append(
                {
                    "title": title,
                    "url": landing,
                    "published": str(work.get("publication_year") or ""),
                    "authors": authors,
                    "source": "OpenAlex API",
                    "venue": venue,
                    "pdf_url": pdf_link,
                }
            )
            seen_titles.add(title.lower())
            if len(entries) >= max_papers:
                return entries
    return entries


def dblp_query_urls(keywords: list[str], max_results: int) -> list[str]:
    # One broad request is intentionally used to respect DBLP rate limits. The
    # response is filtered locally to the configured top-conference venues.
    useful = [keyword for keyword in keywords if keyword.lower() not in {"python", "pytorch"}]
    phrase = " ".join(useful[:2]) or "computer science"
    return [f"https://dblp.org/search/publ/api?q={quote_plus(phrase)}&format=json&h={max_results}"]


def fetch_dblp_payloads(keywords: list[str], max_results: int, timeout: int) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    errors: list[Exception] = []
    for idx, url in enumerate(dblp_query_urls(keywords, max_results)):
        try:
            raw = fetch_bytes(url, timeout, "tech-candidate-sourcing/0.2", attempts=2)
            payload = json.loads(raw.decode("utf-8", errors="replace"))
            if isinstance(payload, dict):
                payloads.append(payload)
        except Exception as exc:
            errors.append(exc)
        if idx + 1 < len(dblp_query_urls(keywords, max_results)):
            time.sleep(1.5)
    if not payloads and errors:
        raise errors[-1]
    return payloads


def venue_label(value: object) -> str:
    values = value if isinstance(value, list) else [value]
    for item in values:
        lowered = str(item or "").strip().lower()
        for token, label in TOP_CONFERENCE_VENUES.items():
            if lowered == token or lowered.startswith(token + " "):
                return label
    return ""


def author_name(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("text") or ""
    return re.sub(r"\s+\d{4}$", "", str(value or "").strip())


def parse_dblp_entries(payloads: list[dict[str, object]], max_papers: int) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    seen_titles: set[str] = set()
    for payload in payloads:
        result = payload.get("result") if isinstance(payload, dict) else None
        hits_wrapper = result.get("hits") if isinstance(result, dict) else None
        hits = hits_wrapper.get("hit", []) if isinstance(hits_wrapper, dict) else []
        if isinstance(hits, dict):
            hits = [hits]
        for hit in hits if isinstance(hits, list) else []:
            info = hit.get("info") if isinstance(hit, dict) else None
            if not isinstance(info, dict):
                continue
            venue = venue_label(info.get("venue"))
            title = str(info.get("title") or "").strip()
            if not venue or not title or title.lower() in seen_titles:
                continue
            author_wrapper = info.get("authors")
            raw_authors = author_wrapper.get("author", []) if isinstance(author_wrapper, dict) else []
            if not isinstance(raw_authors, list):
                raw_authors = [raw_authors]
            authors = []
            for value in raw_authors:
                name = author_name(value)
                if not name:
                    continue
                pid = str(value.get("@pid") or "") if isinstance(value, dict) else ""
                authors.append({"name": name, "pid": pid, "affiliation": "", "urls": []})
            paper_url = str(info.get("ee") or info.get("url") or "")
            entries.append(
                {
                    "title": title,
                    "url": paper_url,
                    "published": str(info.get("year") or ""),
                    "authors": authors,
                    "source": "DBLP API",
                    "venue": venue,
                }
            )
            seen_titles.add(title.lower())
            if len(entries) >= max_papers:
                return entries
    return entries


def enrich_dblp_author_profiles(entries: list[dict[str, object]], limit: int, timeout: int) -> int:
    enriched = 0
    seen: set[str] = set()
    for entry in entries:
        if deadline_reached():
            return enriched
        authors = entry.get("authors") or []
        for author in authors if isinstance(authors, list) else []:
            if enriched >= limit:
                return enriched
            if not isinstance(author, dict):
                continue
            pid = str(author.get("pid") or "").strip()
            if not pid or pid in seen:
                continue
            seen.add(pid)
            try:
                raw = fetch_bytes(f"https://dblp.org/pid/{pid}.xml", timeout, "tech-candidate-sourcing/0.2", attempts=2)
                root = ET.fromstring(raw)
            except Exception:
                continue
            person = next((child for child in root if local_name(child.tag) == "person"), None)
            if person is None:
                continue
            affiliations: list[str] = []
            urls: list[str] = []
            for child in person:
                value = text_of(child)
                if local_name(child.tag) == "note" and child.attrib.get("type") == "affiliation" and value:
                    affiliations.append(value)
                if local_name(child.tag) == "url" and value:
                    host = value.lower()
                    if not any(blocked in host for blocked in ["orcid.org", "wikidata.org", "dblp.org"]):
                        urls.append(value)
            author["affiliation"] = "; ".join(dict.fromkeys(affiliations))
            author["urls"] = list(dict.fromkeys(urls))
            enriched += 1
            time.sleep(0.6)
    return enriched


def interleave_entries(*groups: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    seen: set[str] = set()
    index = 0
    while any(index < len(group) for group in groups):
        for group in groups:
            if index >= len(group):
                continue
            entry = group[index]
            key = str(entry.get("title") or "").lower()
            if key and key not in seen:
                seen.add(key)
                merged.append(entry)
        index += 1
    return merged


def search_url(query: str) -> str:
    return f"https://www.bing.com/search?q={quote_plus(query)}"


def scholar_url(query: str) -> str:
    return f"https://scholar.google.com/scholar?q={quote_plus(query)}"


def paper_topics(title: str, keywords: list[str]) -> str:
    lowered = title.lower()
    matched = [keyword for keyword in keywords if keyword.lower() in lowered]
    aliases = [
        (("agent", "智能体"), "agent"),
        (("multi-agent", "multi agent"), "multi-agent"),
        (("reasoning",), "reasoning"),
        (("tool",), "tool use"),
        (("planning",), "planning"),
        (("memory",), "memory"),
        (("evaluation", "benchmark"), "evaluation"),
        (("code", "program"), "code agent"),
        (("scientist", "research"), "ai scientist"),
    ]
    for tokens, label in aliases:
        if any(token in lowered for token in tokens) and label not in matched:
            matched.append(label)
    return "; ".join(matched[:6]) or "; ".join(keywords[:3])


def author_rows(entries: list[dict[str, object]], keywords: list[str], max_authors: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    topic = " ".join(keywords[:3]) if keywords else "machine learning"
    for entry in entries:
        title = str(entry.get("title") or "")
        paper_url = str(entry.get("url") or "")
        pdf_link = str(entry.get("pdf_url") or "")
        published = str(entry.get("published") or "")
        source = str(entry.get("source") or "public publication metadata")
        venue = str(entry.get("venue") or "")
        topics = paper_topics(title, keywords)
        entry_authors: list[object] = []
        entry_names: set[str] = set()
        for author in entry.get("authors") or []:
            display_name = author_name(author)
            if not display_name or display_name.lower() in entry_names:
                continue
            entry_authors.append(author)
            entry_names.add(display_name.lower())
        new_names = entry_names - seen
        # Keep author lists complete for every included paper. A partial list can
        # make a shared PDF email look uniquely attributable when it is not.
        if rows and len(seen) + len(new_names) > max_authors:
            return rows
        for author in entry_authors:
            display_name = author_name(author)
            seen.add(display_name.lower())
            affiliation = str(author.get("affiliation") or "") if isinstance(author, dict) else ""
            author_pid = str(author.get("pid") or "") if isinstance(author, dict) else ""
            author_urls = (author.get("urls") or []) if isinstance(author, dict) else []
            affiliation_hint = f' "{affiliation}"' if affiliation else ""
            homepage_query = f'"{display_name}"{affiliation_hint} "{topic}" (homepage OR email OR lab)'
            academic_query = f'"{display_name}" "{topic}" (site:edu OR site:edu.cn OR site:ac.uk) email'
            scholar_query = f'"{display_name}" "{topic}"'
            rows.extend(
                [
                    {
                        "author": display_name,
                        "paper": title,
                        "published": published,
                        "paper_url": paper_url,
                        "pdf_url": pdf_link,
                        "channel": "author_homepage",
                        "query": homepage_query,
                        "url": search_url(homepage_query),
                        "next_step": "Open likely homepage/lab pages, then pass public pages to source_candidates.py as --seed-url.",
                        "source": source,
                        "venue": venue,
                        "topics": topics,
                        "author_pid": author_pid,
                        "affiliation": affiliation,
                        "author_urls": "; ".join(str(value) for value in author_urls),
                    },
                    {
                        "author": display_name,
                        "paper": title,
                        "published": published,
                        "paper_url": paper_url,
                        "pdf_url": pdf_link,
                        "channel": "academic_author",
                        "query": academic_query,
                        "url": search_url(academic_query),
                        "next_step": "Prefer university/lab pages with visible public emails.",
                        "source": source,
                        "venue": venue,
                        "topics": topics,
                        "author_pid": author_pid,
                        "affiliation": affiliation,
                        "author_urls": "; ".join(str(value) for value in author_urls),
                    },
                    {
                        "author": display_name,
                        "paper": title,
                        "published": published,
                        "paper_url": paper_url,
                        "pdf_url": pdf_link,
                        "channel": "scholar_author",
                        "query": scholar_query,
                        "url": scholar_url(scholar_query),
                        "next_step": "Use Scholar to confirm authorship, then reverse-check homepage/email.",
                        "source": source,
                        "venue": venue,
                        "topics": topics,
                        "author_pid": author_pid,
                        "affiliation": affiliation,
                        "author_urls": "; ".join(str(value) for value in author_urls),
                    },
                ]
            )
    return rows


def render_markdown(jd_path: Path, keywords: list[str], rows: list[dict[str, str]], note: str = "") -> str:
    lines = [
        "# Paper Author Follow-up Queries",
        "",
        f"- JD: `{jd_path}`",
        f"- Keywords: {', '.join(keywords) if keywords else 'none'}",
        "- Boundary: this file contains public search queries only. It does not guess emails or scrape logged-in services.",
    ]
    if note:
        lines.append(f"- Note: {note}")
    lines.extend(
        [
            "",
            "| Author | Channel | Paper | Published | Query | Entry | Suggested next step | Discovery source | Venue | Research topics | Author PID | Public affiliation | Author URLs | PDF URL |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        entry = f"[search]({row['url']})"
        if row.get("paper_url"):
            entry += f"<br>[paper]({row['paper_url']})"
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_md(row["author"]),
                    escape_md(row["channel"]),
                    escape_md(row["paper"]),
                    escape_md(row["published"]),
                    escape_md(row["query"]),
                    entry,
                    escape_md(row["next_step"]),
                    escape_md(row["source"]),
                    escape_md(row["venue"]),
                    escape_md(row["topics"]),
                    escape_md(row["author_pid"]),
                    escape_md(row["affiliation"]),
                    escape_md(row["author_urls"]),
                    escape_md(row["pdf_url"]),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    global ACTIVE_DEADLINE
    parser = argparse.ArgumentParser(description="Generate arXiv, DBLP, and OpenAlex paper-author evidence from a JD.")
    parser.add_argument("--jd", required=True, help="JD markdown/text file.")
    parser.add_argument("--out", required=True, help="Output Markdown file.")
    parser.add_argument("--max-papers", type=int, default=5)
    parser.add_argument("--max-conference-papers", type=int, default=8)
    parser.add_argument("--max-openalex-papers", type=int, default=15)
    parser.add_argument("--max-authors", type=int, default=12)
    parser.add_argument("--dblp-author-profile-limit", type=int, default=15)
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--offline-xml", default="", help="Optional local arXiv Atom XML fixture for tests.")
    parser.add_argument("--offline-dblp-json", default="", help="Optional local DBLP JSON fixture for tests.")
    parser.add_argument("--offline-openalex-json", default="", help="Optional local OpenAlex JSON fixture for tests.")
    parser.add_argument("--no-arxiv", action="store_true", help="Skip direct arXiv API lookup.")
    parser.add_argument("--no-dblp", action="store_true", help="Skip DBLP top-conference metadata lookup.")
    parser.add_argument("--no-openalex", action="store_true", help="Skip OpenAlex open-access work lookup.")
    parser.add_argument("--deadline-seconds", type=int, default=0, help="Graceful internal deadline; writes partial metadata output.")
    args = parser.parse_args()
    ACTIVE_DEADLINE = time.monotonic() + args.deadline_seconds if args.deadline_seconds > 0 else 0.0

    jd_path = Path(args.jd)
    jd_text = jd_path.read_text(encoding="utf-8")
    keywords = jd_keywords(jd_text, max_keywords=8)
    notes: list[str] = []

    if deadline_reached():
        xml_text = ""
        notes.append("Internal deadline reached before arXiv lookup.")
    elif args.no_arxiv:
        xml_text = ""
    elif args.offline_xml:
        xml_text = Path(args.offline_xml).read_text(encoding="utf-8")
    else:
        try:
            xml_text = fetch_arxiv_xml(keywords, args.max_papers, args.timeout)
        except Exception as exc:
            xml_text = ""
            notes.append(f"arXiv lookup failed: {exc}")

    arxiv_entries = parse_arxiv_entries(xml_text) if xml_text else []
    dblp_entries: list[dict[str, object]] = []
    if not args.no_dblp and not deadline_reached():
        try:
            if args.offline_dblp_json:
                payloads = [json.loads(Path(args.offline_dblp_json).read_text(encoding="utf-8"))]
            else:
                payloads = fetch_dblp_payloads(keywords, max(args.max_conference_papers * 12, 40), args.timeout)
            dblp_entries = parse_dblp_entries(payloads, args.max_conference_papers)
            enrich_dblp_author_profiles(dblp_entries, args.dblp_author_profile_limit, args.timeout)
        except Exception as exc:
            notes.append(f"DBLP lookup failed: {exc}")

    openalex_entries: list[dict[str, object]] = []
    if not args.no_openalex and not deadline_reached():
        try:
            if args.offline_openalex_json:
                payloads = [json.loads(Path(args.offline_openalex_json).read_text(encoding="utf-8"))]
            else:
                payloads = fetch_openalex_payloads(keywords, args.max_openalex_papers, args.timeout)
            openalex_entries = parse_openalex_entries(payloads, args.max_openalex_papers, keywords=keywords)
            notes.extend(OPENALEX_NOTES)
        except Exception as exc:
            notes.append(f"OpenAlex lookup failed: {exc}")
            notes.extend(OPENALEX_NOTES)

    entries = interleave_entries(arxiv_entries, dblp_entries, openalex_entries)
    if deadline_reached():
        notes.append("Internal deadline reached; paper metadata output may be partial.")
    if not entries and not (args.no_arxiv and args.no_dblp and args.no_openalex):
        notes.append(f"All enabled paper metadata sources returned zero relevant papers for keywords: {', '.join(keywords) or 'none'}")
    rows = author_rows(entries, keywords, args.max_authors)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    source_note = (
        f"arXiv papers: {len(arxiv_entries)}; "
        f"DBLP top-conference papers: {len(dblp_entries)}; "
        f"OpenAlex open-PDF papers: {len(openalex_entries)}"
    )
    if notes:
        source_note += "; " + "; ".join(notes)
    out_path.write_text(render_markdown(jd_path, keywords, rows, note=source_note), encoding="utf-8")
    print(f"Wrote {len(rows)} paper-author query rows to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
