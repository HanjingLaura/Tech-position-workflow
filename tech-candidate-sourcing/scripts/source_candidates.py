#!/usr/bin/env python3
"""Source public technical candidates from a JD.

Input: a JD markdown/text file.
Output: a Markdown table of candidate leads with public emails.

This MVP intentionally does not guess email addresses. It only records an email
when it appears directly on a fetched public page or in common public obfuscated
forms such as "name [at] domain [dot] edu".
"""

from __future__ import annotations

import argparse
import base64
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from bs4 import FeatureNotFound

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from safe_http import safe_get_public
from jd_utils import jd_keywords as extract_jd_keywords
from candidate_schema import EMAIL_RE, STANDARD_HEADERS
from china_signals import china_public_evidence, has_china_public_signal
from md_table import escape_md as md_escape


USER_AGENT = "Mozilla/5.0 tech-candidate-sourcing/0.1"
COLLECT_PHONES = False
ACTIVE_DEADLINE = 0.0
SEARCH_NOTES: list[str] = []


class GitHubRateLimitError(RuntimeError):
    pass
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3,4}[\s.-]?\d{4}(?!\d)"
)

STOPWORDS = {
    "and",
    "are",
    "for",
    "from",
    "have",
    "that",
    "the",
    "this",
    "with",
    "以及",
    "负责",
    "岗位",
    "方向",
    "相关",
    "经验",
    "熟悉",
    "能力",
    "优先",
    "研究",
    "工程",
}

BASE_TECH_TERMS = [
    "machine learning",
    "deep learning",
    "large language model",
    "llm",
    "agent",
    "multi-agent",
    "tool use",
    "reasoning",
    "planning",
    "reinforcement learning",
    "nlp",
    "computer vision",
    "pytorch",
    "python",
    "infrastructure",
    "distributed systems",
    "database",
    "security",
    "frontend",
    "backend",
    "data engineering",
]

QUICK_QUERY_TEMPLATES = [
    'site:github.io "{kw}" "email"',
    'site:edu "{kw}" "email"',
    'site:edu.cn "{kw}" ("email" OR "邮箱")',
    'site:ac.uk "{kw}" "email"',
    '"{kw}" "Email" "Google Scholar"',
]

DEEP_QUERY_TEMPLATES = QUICK_QUERY_TEMPLATES + [
    'site:github.io "{kw}" "PhD student" "email"',
    'site:edu "{kw}" "professor" "email"',
    'site:edu "{kw}" "students" "email"',
    'site:edu "{kw}" "lab" "email"',
    'site:edu.cn "{kw}" ("博士生" OR "学生" OR "团队" OR "实验室") ("邮箱" OR "email")',
    'site:edu.cn "{kw}" ("教授" OR "老师" OR "导师") ("邮箱" OR "email")',
    'site:ac.uk "{kw}" ("students" OR "people" OR "lab") email',
    'site:edu.cn "{kw}" "Google Scholar" ("邮箱" OR "email")',
]

CHINA_FOCUS_QUERY_TEMPLATES = [
    'site:edu.cn "{kw}" ("博士生" OR "学生" OR "团队" OR "实验室") ("邮箱" OR "email")',
    'site:edu.cn "{kw}" ("教授" OR "老师" OR "导师" OR "课题组") ("邮箱" OR "email")',
    'site:cn "{kw}" ("个人主页" OR "学术主页" OR "Google Scholar") ("邮箱" OR "email")',
    'site:tsinghua.edu.cn "{kw}" ("邮箱" OR "email")',
    'site:pku.edu.cn "{kw}" ("邮箱" OR "email")',
    'site:sjtu.edu.cn "{kw}" ("邮箱" OR "email")',
    'site:zju.edu.cn "{kw}" ("邮箱" OR "email")',
    'site:ustc.edu.cn "{kw}" ("邮箱" OR "email")',
    'site:fudan.edu.cn "{kw}" ("邮箱" OR "email")',
]

LOW_CONFIDENCE_CHANNELS = {"github_commit"}
GENERIC_EMAIL_PREFIXES = {
    "admin",
    "admission",
    "admissions",
    "asktheprovost",
    "contact",
    "extendedcampus",
    "families",
    "grad",
    "hello",
    "help",
    "hr",
    "info",
    "jobs",
    "onestop",
    "name",
    "news",
    "office",
    "oia",
    "osso",
    "press",
    "recruiting",
    "studyabroad",
    "support",
    "username",
    "webmaster",
    "your_email",
}

NON_CANDIDATE_PAGE_TOKENS = [
    "admission",
    "admissions",
    "ask-the-provost",
    "extended-campus",
    "family-engagement",
    "families",
    "graduate/index",
    "international-affairs",
    "login",
    "office of",
    "office-of",
    "one stop",
    "onestop",
    "provost",
    "sign in",
    "student resources",
    "summer sessions",
    "summer-sessions",
]

ACADEMIC_INDEX_TOKENS = [
    "people",
    "student",
    "students",
    "faculty",
    "team",
    "members",
    "member",
    "lab",
    "group",
    "导师",
    "老师",
    "教师",
    "教授",
    "学生",
    "博士",
    "博士生",
    "硕士",
    "硕士生",
    "团队",
    "成员",
    "实验室",
    "课题组",
]

ACADEMIC_ROSTER_LINK_TOKENS = [
    "people",
    "students",
    "student",
    "members",
    "member",
    "team",
    "lab",
    "group",
    "alumni",
    "current students",
    "phd students",
    "team members",
    "lab members",
    "research group",
    "导师",
    "老师",
    "教师",
    "教授",
    "学生",
    "博士生",
    "硕士生",
    "团队",
    "成员",
    "实验室",
    "课题组",
    "校友",
]

PERSON_LINK_PATH_TOKENS = [
    "/people/",
    "/person/",
    "/faculty/",
    "/students/",
    "/student/",
    "/profiles/",
    "/profile/",
    "/user/",
    "/member/",
    "/members/",
    "/teacher/",
    "/teachers/",
    "/team/",
    "/staff/",
    "/导师/",
    "/教师/",
    "/学生/",
    "/成员/",
    "/团队/",
]

BAD_LINK_LABELS = {
    "home",
    "people",
    "research",
    "publications",
    "software",
    "seminar",
    "join",
    "contact",
    "students",
    "faculty",
    "news",
    "events",
    "首页",
    "主页",
    "研究",
    "论文",
    "发表",
    "项目",
    "软件",
    "新闻",
    "动态",
    "招聘",
    "加入我们",
    "联系我们",
    "成员",
    "团队",
    "学生",
    "教师",
    "导师",
    "更多",
}


@dataclass
class Candidate:
    name: str
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    source_urls: set[str] = field(default_factory=set)
    matched_keywords: list[str] = field(default_factory=list)
    basic_info: str = "见来源页"
    channel: str = "public_web"
    lead_type: str = "unknown"
    confidence: str = "low"
    score: int = 0
    recommendation: str = "待确认"


def fetch(url: str, timeout: int = 6) -> str:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    response = safe_get_public(url, headers=headers, timeout=timeout, max_bytes=3 * 1024 * 1024)
    declared = requests.utils.get_encoding_from_headers(response.headers)
    if not declared or declared.lower() in {"iso-8859-1", "latin-1"}:
        try:
            response.content.decode("utf-8")
            response.encoding = "utf-8"
        except UnicodeDecodeError:
            host = (urlparse(url).hostname or "").lower()
            if host.endswith(".cn"):
                try:
                    response.content.decode("gb18030")
                    response.encoding = "gb18030"
                except UnicodeDecodeError:
                    response.encoding = response.apparent_encoding or "utf-8"
            else:
                response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def soup_from_html(raw_html: str, parser: str = "html.parser") -> BeautifulSoup:
    try:
        return BeautifulSoup(raw_html, parser)
    except FeatureNotFound:
        return BeautifulSoup(raw_html, "html.parser")
    except Exception:
        # Some public pages return malformed binary-like text with invalid numeric
        # character references. Escaping `&#` keeps one bad page from aborting a
        # whole sourcing pass.
        try:
            return BeautifulSoup(raw_html.replace("&#", "&amp;#"), parser)
        except FeatureNotFound:
            return BeautifulSoup(raw_html.replace("&#", "&amp;#"), "html.parser")


def deadline_reached() -> bool:
    return bool(ACTIVE_DEADLINE and time.monotonic() >= ACTIVE_DEADLINE)


def add_search_note(note: str) -> None:
    if note not in SEARCH_NOTES:
        SEARCH_NOTES.append(note)


def github_api_headers() -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_api_json(url: str, timeout: int = 8):
    try:
        response = requests.get(url, headers=github_api_headers(), timeout=timeout)
    except Exception as exc:
        add_search_note(f"GitHub API request failed: {type(exc).__name__}: {exc}")
        raise
    if response.status_code in {403, 429}:
        remaining = response.headers.get("X-RateLimit-Remaining", "unknown")
        reset = response.headers.get("X-RateLimit-Reset", "unknown")
        token_hint = " Set GITHUB_TOKEN or GH_TOKEN for a higher public-read limit." if not (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")) else ""
        note = f"GitHub API rate limited ({response.status_code}); remaining={remaining}, reset={reset}.{token_hint}"
        add_search_note(note)
        raise GitHubRateLimitError(note)
    try:
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        add_search_note(f"GitHub API error for {url}: {type(exc).__name__}: {exc}")
        raise


def clean_text(raw_html: str) -> str:
    soup = soup_from_html(raw_html)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return html.unescape(" ".join(soup.get_text(" ").split()))


def decode_bing_href(href: str) -> str:
    parsed = urlparse(href)
    if "bing.com" not in parsed.netloc or not parsed.path.startswith("/ck"):
        return href
    encoded_values = parse_qs(parsed.query).get("u", [])
    if not encoded_values:
        return href
    encoded = encoded_values[0]
    if encoded.startswith("a1"):
        encoded = encoded[2:]
    try:
        padding = "=" * ((4 - len(encoded) % 4) % 4)
        return base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
    except Exception:
        return href


def title_from_html(raw_html: str) -> str | None:
    soup = soup_from_html(raw_html)
    if soup.title and soup.title.string:
        title = " ".join(soup.title.string.split())
        return title[:90]
    return None


def extract_emails(text: str) -> set[str]:
    normalized = html.unescape(text)
    emails = set(EMAIL_RE.findall(normalized))

    obfuscated_pattern = re.compile(
        r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+)\s*(?:\[at\]|\(at\))\s*"
        r"([A-Za-z0-9-]+(?:\s*(?:\[dot\]|\(dot\))\s*[A-Za-z0-9-]+)+)",
        re.IGNORECASE,
    )
    for user, obfuscated_domain in obfuscated_pattern.findall(normalized):
        domain_parts = [
            part.strip()
            for part in re.split(r"\s*(?:\[dot\]|\(dot\))\s*", obfuscated_domain, flags=re.IGNORECASE)
            if part.strip()
        ]
        if len(domain_parts) >= 2 and re.fullmatch(r"[A-Za-z]{2,63}", domain_parts[-1]):
            emails.add(f"{user}@{'.'.join(domain_parts)}")

    blocked = {"git@github.com"}
    cleaned = set()
    for email in emails:
        email = email.replace("u003e", "").strip(" <>.,;:()[]{}")
        lower = email.lower()
        if lower in blocked or lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            continue
        if (
            "example" in lower
            or "contoso.com" in lower
            or "test.com" in lower
            or "localhost" in lower
            or "yourname" in lower
            or "noreply" in lower
            or "googlegroups.com" in lower
            or "ingest.sentry.io" in lower
        ):
            continue
        local_part = lower.split("@", 1)[0]
        if local_part in GENERIC_EMAIL_PREFIXES:
            continue
        if local_part in {"firstname", "lastname", "first.last", "first_last", "your.name"}:
            continue
        if local_part.startswith(("your_", "your.", "username", "k-state")):
            continue
        cleaned.add(email)
    return cleaned


def identity_tokens(name: str) -> list[str]:
    blocked = {
        "academic",
        "computer",
        "department",
        "group",
        "home",
        "homepage",
        "laboratory",
        "lab",
        "machine",
        "nlp",
        "people",
        "profile",
        "professor",
        "research",
        "stanford",
        "university",
    }
    tokens = []
    for token in re.findall(r"[A-Za-z0-9]+", name.lower()):
        if len(token) >= 3 and token not in blocked and token not in tokens:
            tokens.append(token)
    return tokens


def identity_email_score(email: str, name: str, url: str = "") -> int:
    tokens = identity_tokens(name)
    if not tokens:
        return 0
    local = email.split("@", 1)[0].lower()
    local_compact = re.sub(r"[^a-z0-9]", "", local)
    url_path = urlparse(url).path.lower()
    score = 0
    for token in tokens:
        if token in local_compact or local_compact in token:
            score += 4
        if token in url_path:
            score += 1
    if len(tokens) >= 2:
        first, last = tokens[0], tokens[-1]
        if f"{first[0]}{last}" in local_compact or f"{first}.{last}" in local:
            score += 5
        if last in local_compact:
            score += 3
    return score


def filter_identity_emails(emails: set[str], name: str, url: str = "") -> set[str]:
    if len(emails) <= 1:
        return emails
    scored = [(identity_email_score(email, name, url), email) for email in emails]
    best_score = max(score for score, _email in scored)
    if best_score <= 0:
        return emails
    return {email for score, email in scored if score == best_score}


def is_likely_automation_identity(cand: Candidate) -> bool:
    identity = " ".join(
        [
            cand.name,
            cand.basic_info,
            " ".join(cand.emails),
            " ".join(cand.source_urls),
        ]
    ).lower()
    automation_tokens = [
        "[bot]",
        "-bot",
        "_bot",
        "dependabot",
        "renovate",
        "github-actions",
        "github actions",
        "actions-user",
        "bot@",
    ]
    return any(token in identity for token in automation_tokens)


def extract_phones(text: str) -> set[str]:
    if not COLLECT_PHONES:
        return set()
    phones = set()
    for match in PHONE_RE.findall(text):
        normalized = re.sub(r"\s+", " ", match).strip(" .-()")
        digits = re.sub(r"\D", "", normalized)
        if len(digits) < 10 or len(digits) > 15:
            continue
        if re.fullmatch(r"(?:19|20)\d{2}[\s.-](?:19|20)\d{2}", normalized):
            continue
        if len(set(digits)) <= 2:
            continue
        phones.add(normalized)
    return phones


def search_bing(query: str, limit: int = 5) -> list[str]:
    url = f"https://www.bing.com/search?q={quote_plus(query)}"
    try:
        soup = BeautifulSoup(fetch(url), "html.parser")
    except Exception as exc:
        add_search_note(f"Bing search failed for {query!r}: {type(exc).__name__}: {exc}")
        return []

    urls: list[str] = []
    for link in soup.select("li.b_algo h2 a, h2 a"):
        href = link.get("href")
        if not href:
            continue
        href = decode_bing_href(href)
        host = urlparse(href).netloc.lower()
        if not host or "bing.com" in host:
            continue
        if href not in urls:
            urls.append(href)
        if len(urls) >= limit:
            break
    if not urls:
        add_search_note(f"Bing returned no parseable results for {query!r}; CAPTCHA or markup changes may be involved.")
    return urls


def normalize_search_href(href: str) -> str | None:
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg", [])
        if uddg:
            href = unquote(uddg[0])
            parsed = urlparse(href)
    if parsed.scheme not in {"http", "https"}:
        return None
    if any(blocked in parsed.netloc.lower() for blocked in ["bing.com", "duckduckgo.com"]):
        return None
    return href


def normalize_public_url(url: str) -> str | None:
    url = url.strip()
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    if not re.match(r"https?://", url, re.IGNORECASE):
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return url


def is_non_candidate_page(text: str = "", url: str = "") -> bool:
    parsed = urlparse(url)
    combined = f"{text} {parsed.netloc} {parsed.path}".lower()
    return any(token in combined for token in NON_CANDIDATE_PAGE_TOKENS)


def search_duckduckgo(query: str, limit: int = 5) -> list[str]:
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        soup = BeautifulSoup(fetch(url), "html.parser")
    except Exception as exc:
        add_search_note(f"DuckDuckGo search failed for {query!r}: {type(exc).__name__}: {exc}")
        return []

    urls: list[str] = []
    for link in soup.select("a.result__a, a.result-link"):
        href = normalize_search_href(link.get("href", ""))
        if not href:
            continue
        if href not in urls:
            urls.append(href)
        if len(urls) >= limit:
            break
    if not urls:
        add_search_note(f"DuckDuckGo returned no parseable results for {query!r}; CAPTCHA or markup changes may be involved.")
    return urls


def search_web(query: str, limit: int = 5) -> list[str]:
    urls: list[str] = []
    for search_fn in [search_bing, search_duckduckgo]:
        for url in search_fn(query, limit=limit):
            if url not in urls:
                urls.append(url)
            if len(urls) >= limit:
                return urls
    if not urls:
        add_search_note(f"All configured HTML search providers returned zero results for {query!r}.")
    return urls


def search_github_repositories(keywords: list[str], limit: int = 6) -> list[str]:
    if not keywords:
        return []
    query_terms = [kw for kw in keywords[:3] if len(kw) <= 40]
    query = " ".join(query_terms) + " in:name,description,readme"
    api_url = f"https://api.github.com/search/repositories?q={quote_plus(query)}&sort=stars&order=desc&per_page={limit}"
    try:
        payload = github_api_json(api_url, timeout=8)
    except Exception:
        return []
    items = payload.get("items", []) if isinstance(payload, dict) else []
    urls: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        relevance_text = " ".join(
            str(item.get(key) or "")
            for key in ["name", "full_name", "description"]
        ).lower()
        if not any(keyword.lower() in relevance_text for keyword in keywords[:3]):
            continue
        html_url = item.get("html_url")
        if html_url and html_url not in urls:
            urls.append(html_url)
    return urls


def is_academic_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    return bool(
        host.endswith(".edu")
        or re.search(r"(?:^|\.)edu\.[a-z]{2}$", host)
        or host.endswith(".ac.uk")
        or re.search(r"(?:^|\.)ac\.[a-z]{2}$", host)
    )


def is_candidate_source_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if not host:
        return False
    blocked_hosts = [
        "wikipedia.org",
        "youtube.com",
        "zhihu.com",
        "runoob.com",
        "csdn.net",
        "linkedin.com",
        "microsoft.github.io",
    ]
    if any(blocked in host for blocked in blocked_hosts):
        return False
    if is_non_candidate_page(url=url):
        return False
    if host == "github.io" or host.endswith(".github.io"):
        return True
    if is_academic_host(host):
        return True
    if host == "github.com" and path.count("/") >= 2:
        return True
    return False


def classify_source_url(url: str) -> tuple[str, str, str]:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if host == "github.com":
        return ("github", "github_repo", "low")
    if host == "github.io" or host.endswith(".github.io"):
        return ("public_web", "personal_homepage", "medium")
    if is_academic_host(host):
        if any(token in path for token in ["lab", "group", "people", "student", "team", "member", "成员", "团队", "实验室", "课题组"]):
            return ("academic_web", "lab_or_student_page", "medium")
        if any(token in path for token in ["faculty", "prof", "people", "directory", "teacher", "导师", "教师", "教授"]):
            return ("academic_web", "faculty_page", "medium")
        return ("academic_web", "academic_page", "medium")
    return ("public_web", "public_page", "low")


def jd_keywords(jd_text: str, max_keywords: int = 6) -> list[str]:
    return extract_jd_keywords(jd_text, max_keywords=max_keywords)


def read_seed_file(path: str) -> list[str]:
    seed_path = Path(path)
    if not seed_path.exists():
        return []
    urls: list[str] = []
    for line in seed_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def discover_urls(
    jd_text: str,
    search_limit: int,
    seed_urls: Iterable[str],
    seed_files: Iterable[str],
    no_search: bool,
    max_urls: int,
    depth: str,
    github_search: bool,
    focus: str = "general",
) -> list[str]:
    urls: list[str] = []
    for url in seed_urls:
        if deadline_reached():
            return urls
        if len(urls) >= max_urls:
            break
        if url not in urls:
            urls.append(url)
    for seed_file in seed_files:
        for url in read_seed_file(seed_file):
            if deadline_reached():
                return urls
            if len(urls) >= max_urls:
                break
            if url not in urls:
                urls.append(url)

    if max_urls <= 0:
        return []

    if no_search:
        return urls

    templates = list(DEEP_QUERY_TEMPLATES if depth == "deep" else QUICK_QUERY_TEMPLATES)
    if focus == "china":
        templates = CHINA_FOCUS_QUERY_TEMPLATES[:6] + templates[:6]
    keyword_limit = 6 if depth == "deep" else 3
    keywords = jd_keywords(jd_text, max_keywords=keyword_limit)

    if github_search and len(urls) < max_urls:
        github_urls = search_github_repositories(keywords, limit=max_urls - len(urls))
        for url in github_urls:
            if url not in urls:
                urls.append(url)
            if len(urls) >= max_urls:
                return urls
        if depth == "quick" and github_urls:
            return urls

    for keyword in keywords:
        for template in templates:
            if deadline_reached():
                add_search_note("Internal deadline reached during URL discovery; returning partial results.")
                return urls
            if len(urls) >= max_urls:
                return urls
            query = template.format(kw=keyword)
            for url in search_web(query, limit=max(search_limit * 5, search_limit)):
                if not is_candidate_source_url(url):
                    continue
                if url not in urls:
                    urls.append(url)
                if len(urls) >= max_urls:
                    return urls
            time.sleep(0.4)

    return urls


def likely_name(raw_html: str, text: str, url: str) -> str:
    title = title_from_html(raw_html)
    if title:
        title = re.sub(
            r"\s*[-|]\s*(Home|Homepage|Academic Homepage|GitHub|个人主页|学术主页|主页|首页).*$",
            "",
            title,
            flags=re.IGNORECASE,
        )
        if 2 <= len(title) <= 80:
            return title

    first_text = re.search(r"^([^|<>\n]{2,80})", text.strip())
    if first_text:
        first = re.sub(r"\s+", " ", first_text.group(1).strip(" -"))
        if 2 <= len(first) <= 60:
            return first

    slug = Path(urlparse(url).path).name or urlparse(url).netloc
    return slug.replace("-", " ").replace("_", " ").title()


def is_academic_index_page(url: str, raw_html: str, text: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if not (is_academic_host(host) or "github.io" in host):
        return False
    if any(token in path for token in ACADEMIC_INDEX_TOKENS):
        return True
    title = title_from_html(raw_html) or ""
    if any(token in title.lower() for token in ACADEMIC_INDEX_TOKENS):
        return True
    return len(re.findall(r"<a\s+[^>]*href=", raw_html, re.IGNORECASE)) >= 15 and any(token in text.lower() for token in ACADEMIC_INDEX_TOKENS)


def looks_like_chinese_person_label(label: str) -> bool:
    compact = re.sub(r"\s+", "", label)
    if compact in BAD_LINK_LABELS:
        return False
    if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", compact):
        return True
    return bool(
        re.fullmatch(
            r"[\u4e00-\u9fff]{2,4}(博士生|博士|硕士生|硕士|学生|教授|老师|导师|研究员|助理教授|副教授|工程师)?",
            compact,
        )
    )


def looks_like_person_link(text: str, href: str) -> bool:
    label = " ".join(text.split()).strip()
    if not label or len(label) > 80:
        return False
    lowered_href = href.lower()
    lowered_label = label.lower()
    if lowered_label in BAD_LINK_LABELS:
        return False
    if is_non_candidate_page(label, href):
        return False
    if any(token in lowered_href for token in ["mailto:", "javascript:", "#", ".pdf", ".jpg", ".png"]):
        return False
    if "~" in href or any(token in lowered_href for token in PERSON_LINK_PATH_TOKENS):
        return True
    if looks_like_chinese_person_label(label):
        return True
    words = re.findall(r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'-]+", label)
    return len(words) >= 2 and len(label.split()) <= 5


def academic_profile_links(raw_html: str, parent_url: str, limit: int) -> list[tuple[str, str]]:
    soup = soup_from_html(raw_html)
    parent_host = urlparse(parent_url).netloc.lower()
    links: list[tuple[str, str]] = []
    for link in soup.select("a[href]"):
        label = link.get_text(" ", strip=True)
        href = link.get("href", "")
        if not looks_like_person_link(label, href):
            continue
        absolute = urljoin(parent_url, href)
        parsed = urlparse(absolute)
        host = parsed.netloc.lower()
        if not parsed.scheme.startswith("http"):
            continue
        if host != parent_host and not (is_academic_host(host) or "github.io" in host):
            continue
        item = (label, absolute)
        if item not in links:
            links.append(item)
        if len(links) >= limit:
            break
    return links


def looks_like_academic_roster_link(label: str, href: str) -> bool:
    label = " ".join(label.split()).strip()
    if not label or len(label) > 90:
        return False
    lowered_href = href.lower()
    if any(token in lowered_href for token in ["mailto:", "javascript:", "#", ".pdf", ".jpg", ".png"]):
        return False
    combined = f"{label} {urlparse(href).path}".lower()
    return any(token in combined for token in ACADEMIC_ROSTER_LINK_TOKENS)


def academic_roster_links(raw_html: str, parent_url: str, limit: int) -> list[tuple[str, str]]:
    soup = soup_from_html(raw_html)
    parent_host = urlparse(parent_url).netloc.lower()
    links: list[tuple[str, str]] = []
    for link in soup.select("a[href]"):
        label = link.get_text(" ", strip=True)
        href = link.get("href", "")
        if not looks_like_academic_roster_link(label, href):
            continue
        absolute = urljoin(parent_url, href)
        parsed = urlparse(absolute)
        host = parsed.netloc.lower()
        if not parsed.scheme.startswith("http"):
            continue
        if host != parent_host and not (is_academic_host(host) or "github.io" in host):
            continue
        if is_non_candidate_page(label, absolute):
            continue
        item = (label, absolute)
        if item not in links:
            links.append(item)
        if len(links) >= limit:
            break
    return links


def academic_link_candidates(
    parent_url: str,
    parent_raw: str,
    keywords: list[str],
    link_limit: int,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for label, profile_url in academic_profile_links(parent_raw, parent_url, link_limit):
        if deadline_reached():
            break
        try:
            raw = fetch(profile_url)
        except Exception:
            continue
        text = clean_text(raw)
        score, matched = score_text(text, keywords)
        phones = extract_phones(text)
        name = likely_name(raw, text, profile_url)
        if name.lower() in {"home", "homepage"}:
            name = label
        if is_non_candidate_page(name, profile_url):
            continue
        emails = filter_identity_emails(extract_emails(raw + " " + text), name, profile_url)
        if not emails:
            continue
        candidates.append(
            Candidate(
                name=name,
                emails=emails,
                phones=phones,
                source_urls={parent_url, profile_url},
                matched_keywords=matched,
                basic_info=infer_organization(text) if infer_organization(text) != "见来源页" else f"Academic profile linked from {urlparse(parent_url).netloc}",
                channel="academic_web",
                lead_type="academic_profile",
                confidence="medium",
                score=max(score, 68),
                recommendation=recommendation(max(score, 68)),
            )
        )
        time.sleep(0.1)
    return candidates


def academic_roster_page_candidates(
    parent_url: str,
    parent_raw: str,
    keywords: list[str],
    link_limit: int,
    page_limit: int = 5,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    per_page_link_limit = max(5, link_limit // 2)
    for _label, roster_url in academic_roster_links(parent_raw, parent_url, page_limit):
        if deadline_reached():
            break
        try:
            raw = fetch(roster_url)
        except Exception:
            continue
        candidates.extend(academic_link_candidates(roster_url, raw, keywords, per_page_link_limit))
        time.sleep(0.1)
    return candidates


def infer_organization(text: str) -> str:
    patterns = [
        r"(PhD student|Ph\.D\. student|doctoral student|professor|research scientist|student|engineer).{0,80}",
        r"(University|Institute|Laboratory|Lab|Microsoft|Google|Meta|OpenAI|Anthropic|Stanford|CMU|MIT|Tsinghua|Peking).{0,80}",
        r"(博士生|博士|硕士生|硕士|教授|副教授|助理教授|导师|老师|教师|研究员|工程师).{0,80}",
        r"(大学|学院|研究院|实验室|课题组|团队|清华|北大|北京大学|上海交大|浙江大学|中科院).{0,80}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return " ".join(match.group(0).split())[:120]
    return "见来源页"


def score_text(text: str, keywords: list[str]) -> tuple[int, list[str]]:
    lowered = text.lower()
    matched = []
    for keyword in keywords:
        if keyword.lower() in lowered and keyword not in matched:
            matched.append(keyword)

    score = 45 + min(len(matched) * 7, 35)
    strong_signals = [
        "phd",
        "ph.d",
        "paper",
        "publication",
        "github",
        "benchmark",
        "open source",
        "conference",
        "neurips",
        "iclr",
        "icml",
        "acl",
        "emnlp",
        "aaai",
        "ijcai",
        "论文",
        "发表",
        "博士",
        "硕士",
        "教授",
        "学生",
        "导师",
        "实验室",
        "课题组",
        "开源",
        "顶会",
    ]
    for signal in strong_signals:
        if signal in lowered:
            score += 2
    return min(score, 100), matched[:8]


def recommendation(score: int) -> str:
    if score >= 85:
        return "强推"
    if score >= 70:
        return "可聊"
    if score >= 55:
        return "备选"
    return "不建议"


def candidates_from_url(
    url: str,
    keywords: list[str],
    expand_academic: bool,
    academic_link_limit: int,
    github_contributor_limit: int = 20,
    github_commit_limit: int = 20,
) -> list[Candidate]:
    try:
        raw = fetch(url)
    except Exception:
        return []

    text = clean_text(raw)
    phones = extract_phones(text)
    score, matched = score_text(text, keywords)
    channel, lead_type, confidence = classify_source_url(url)
    linked_candidates: list[Candidate] = []
    if expand_academic:
        is_index = is_academic_index_page(url, raw, text)
        if is_index:
            linked_candidates.extend(academic_link_candidates(url, raw, keywords, academic_link_limit))
            if linked_candidates:
                return linked_candidates
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if is_academic_host(host) or host == "github.io" or host.endswith(".github.io"):
            linked_candidates.extend(academic_roster_page_candidates(url, raw, keywords, academic_link_limit))
    name = likely_name(raw, text, url)
    if is_non_candidate_page(name, url) and not linked_candidates:
        return []
    emails = filter_identity_emails(extract_emails(raw + " " + text), name, url)
    if not emails and is_github_repo_url(url):
        return github_commit_candidates(url, score, matched, keywords, github_contributor_limit, github_commit_limit)
    if not emails:
        return linked_candidates

    direct_candidates = [
        Candidate(
            name=name,
            emails=emails,
            phones=phones,
            source_urls={url},
            matched_keywords=matched,
            basic_info=infer_organization(text),
            channel=channel,
            lead_type=lead_type,
            confidence=confidence,
            score=score,
            recommendation=recommendation(score),
        )
    ]
    return direct_candidates + linked_candidates


def is_github_repo_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "github.com":
        return False
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    blocked = {"issues", "pulls", "actions", "marketplace", "topics", "search"}
    return len(parts) >= 2 and parts[0] not in blocked and parts[1] not in blocked


def github_repo_slug(url: str) -> str | None:
    parts = [part for part in urlparse(url).path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None
    return f"{parts[0]}/{parts[1]}"


def fetch_github_user(login: str, cache: dict[str, dict]) -> dict:
    if deadline_reached():
        add_search_note("Internal deadline reached before GitHub profile fetch; returning partial results.")
        return {}
    if login in cache:
        return cache[login]
    try:
        data = github_api_json(f"https://api.github.com/users/{login}", timeout=6)
        cache[login] = data if isinstance(data, dict) else {}
    except GitHubRateLimitError:
        return {}
    except Exception:
        cache[login] = {}
    return cache[login]


def enrich_from_public_profile(profile_url: str, keywords: list[str]) -> tuple[set[str], set[str], list[str], int]:
    try:
        raw = fetch(profile_url)
    except Exception:
        return set(), set(), [], 0
    text = clean_text(raw)
    emails = extract_emails(raw + " " + text)
    phones = extract_phones(text)
    score, matched = score_text(text, keywords)
    return emails, phones, matched, score


def github_profile_info(user_data: dict) -> str:
    parts = []
    for key in ["name", "company", "location", "bio"]:
        value = str(user_data.get(key) or "").strip()
        if value:
            parts.append(value)
    if not parts and user_data.get("login"):
        parts.append(f"GitHub: {user_data['login']}")
    return "; ".join(parts)[:180] if parts else ""


def github_profile_candidate(
    login: str,
    repo: str,
    source_urls: set[str],
    base_score: int,
    matched: list[str],
    keywords: list[str],
    user_cache: dict[str, dict],
    lead_type: str = "repo_contributor_profile",
    default_basic_info: str | None = None,
    skip_organizations: bool = False,
) -> Candidate | None:
    if deadline_reached():
        return None
    if not login:
        return None
    user_data = fetch_github_user(login, user_cache)
    if not user_data:
        return None
    if skip_organizations and str(user_data.get("type") or "").lower() == "organization":
        return None

    profile_url = str(user_data.get("html_url") or f"https://github.com/{login}").strip()
    if profile_url:
        source_urls.add(profile_url)
    profile_email = str(user_data.get("email") or "").strip()
    public_emails = extract_emails(profile_email)
    phones: set[str] = set()
    all_matched = list(matched)
    score = max(base_score, 65)

    blog_url = normalize_public_url(str(user_data.get("blog") or ""))
    if blog_url and not deadline_reached():
        source_urls.add(blog_url)
        blog_emails, blog_phones, blog_matched, blog_score = enrich_from_public_profile(blog_url, keywords)
        public_emails |= blog_emails
        phones |= blog_phones
        all_matched = list(dict.fromkeys(all_matched + blog_matched))
        score = max(score, blog_score)

    if not public_emails:
        return None

    name = str(user_data.get("name") or login).strip()
    return Candidate(
        name=name,
        emails=public_emails,
        phones=phones,
        source_urls=source_urls,
        matched_keywords=all_matched,
        basic_info=github_profile_info(user_data) or default_basic_info or f"GitHub contributor: {repo}",
        channel="github_profile",
        lead_type=lead_type,
        confidence="medium",
        score=score,
        recommendation=recommendation(score),
    )


def github_repo_owner_profile_candidate(
    repo: str,
    base_score: int,
    matched: list[str],
    keywords: list[str],
    user_cache: dict[str, dict],
) -> Candidate | None:
    owner = repo.split("/", 1)[0].strip()
    if not owner:
        return None
    return github_profile_candidate(
        login=owner,
        repo=repo,
        source_urls={f"https://github.com/{repo}", f"https://github.com/{owner}"},
        base_score=max(base_score, 67),
        matched=matched,
        keywords=keywords,
        user_cache=user_cache,
        lead_type="repo_owner_profile",
        default_basic_info=f"GitHub repository owner: {repo}",
        skip_organizations=True,
    )


def github_contributor_profile_candidates(
    repo: str,
    base_score: int,
    matched: list[str],
    keywords: list[str],
    user_cache: dict[str, dict],
    contributor_limit: int = 20,
) -> list[Candidate]:
    per_page = max(1, min(contributor_limit, 100))
    api_url = f"https://api.github.com/repos/{repo}/contributors?per_page={per_page}"
    try:
        contributors = github_api_json(api_url, timeout=6)
    except Exception:
        return []
    if not isinstance(contributors, list):
        return []

    candidates: dict[str, Candidate] = {}
    for contributor in contributors:
        if deadline_reached():
            add_search_note("Internal deadline reached during GitHub contributor enrichment; returning partial results.")
            break
        if not isinstance(contributor, dict):
            continue
        login = str(contributor.get("login") or "").strip()
        html_url = str(contributor.get("html_url") or "").strip()
        source_urls = {f"https://github.com/{repo}"}
        if html_url:
            source_urls.add(html_url)
        cand = github_profile_candidate(login, repo, source_urls, base_score, matched, keywords, user_cache)
        if not cand:
            continue
        key = next(iter(sorted(cand.emails))).lower()
        candidates[key] = cand
    return list(candidates.values())


def github_user_search_candidates(keywords: list[str], limit: int) -> list[Candidate]:
    if not keywords or limit <= 0:
        return []
    query_terms = [kw for kw in keywords[:4] if len(kw) <= 40]
    query = " ".join(query_terms) + " in:login,fullname"
    api_url = f"https://api.github.com/search/users?q={quote_plus(query)}&sort=followers&order=desc&per_page={limit}"
    try:
        payload = github_api_json(api_url, timeout=8)
    except Exception:
        return []
    items = payload.get("items", []) if isinstance(payload, dict) else []
    user_cache: dict[str, dict] = {}
    candidates: dict[str, Candidate] = {}
    for item in items:
        if deadline_reached():
            add_search_note("Internal deadline reached during GitHub user enrichment; returning partial results.")
            break
        if not isinstance(item, dict):
            continue
        login = str(item.get("login") or "").strip()
        html_url = str(item.get("html_url") or "").strip()
        source_urls = {html_url} if html_url else set()
        cand = github_profile_candidate(
            login=login,
            repo="GitHub user search",
            source_urls=source_urls,
            base_score=65,
            matched=keywords[:3],
            keywords=keywords,
            user_cache=user_cache,
        )
        if not cand:
            continue
        cand.lead_type = "github_user_profile"
        key = next(iter(sorted(cand.emails))).lower()
        candidates[key] = cand
    return list(candidates.values())


def github_commit_candidates(
    url: str,
    base_score: int,
    matched: list[str],
    keywords: list[str],
    contributor_limit: int = 20,
    commit_limit: int = 20,
) -> list[Candidate]:
    repo = github_repo_slug(url)
    if not repo:
        return []
    if deadline_reached():
        return []
    commit_limit = max(1, min(commit_limit, 100))
    user_cache: dict[str, dict] = {}
    profile_candidates: list[Candidate] = []
    owner_candidate = github_repo_owner_profile_candidate(repo, base_score, matched, keywords, user_cache)
    if owner_candidate:
        profile_candidates.append(owner_candidate)
    profile_candidates.extend(
        github_contributor_profile_candidates(repo, base_score, matched, keywords, user_cache, contributor_limit)
    )
    candidates: dict[str, Candidate] = {}
    for cand in profile_candidates:
        if not cand.emails:
            continue
        key = next(iter(sorted(cand.emails))).lower()
        candidates.setdefault(key, cand)
    api_url = f"https://api.github.com/repos/{repo}/commits?per_page={commit_limit}"
    try:
        commits = github_api_json(api_url, timeout=6)
    except Exception:
        return list(candidates.values()) or github_git_log_candidates(url, base_score, matched, commit_limit)
    if not isinstance(commits, list):
        return list(candidates.values()) or github_git_log_candidates(url, base_score, matched, commit_limit)

    for commit in commits:
        if deadline_reached():
            add_search_note("Internal deadline reached during GitHub commit enrichment; returning partial results.")
            break
        commit_info = commit.get("commit", {}) if isinstance(commit, dict) else {}
        html_url = commit.get("html_url", url) if isinstance(commit, dict) else url
        for role in ["author", "committer"]:
            person = commit_info.get(role, {})
            name = str(person.get("name", "")).strip()
            email = str(person.get("email", "")).strip()
            public_emails = extract_emails(email)

            source_urls = {html_url}
            phones: set[str] = set()
            profile_info = ""
            confidence = "low"
            channel = "github_commit"
            lead_type = "repo_contributor"
            score = max(base_score, 60)
            all_matched = list(matched)

            github_user = commit.get(role) if isinstance(commit.get(role), dict) else {}
            login = str(github_user.get("login") or "").strip()
            if login:
                user_data = fetch_github_user(login, user_cache)
                if user_data and not name:
                    name = str(user_data.get("name") or login).strip()
                profile_url = str(user_data.get("html_url") or github_user.get("html_url") or "").strip()
                if profile_url:
                    source_urls.add(profile_url)
                profile_email = str(user_data.get("email") or "").strip()
                public_emails |= extract_emails(profile_email)
                profile_info = github_profile_info(user_data)
                blog_url = normalize_public_url(str(user_data.get("blog") or ""))
                if blog_url:
                    source_urls.add(blog_url)
                    blog_emails, blog_phones, blog_matched, blog_score = enrich_from_public_profile(blog_url, keywords)
                    public_emails |= blog_emails
                    phones |= blog_phones
                    all_matched = list(dict.fromkeys(all_matched + blog_matched))
                    score = max(score, blog_score)
                if profile_info or profile_url:
                    confidence = "medium" if profile_email or blog_url else "low"
                    channel = "github_profile" if confidence == "medium" else "github_commit"
                    lead_type = "repo_contributor_profile" if confidence == "medium" else "repo_contributor"

            if not name or not public_emails:
                continue

            key = sorted(public_emails)[0].lower()
            if key not in candidates:
                candidates[key] = Candidate(
                    name=name,
                    emails=public_emails,
                    phones=phones,
                    source_urls=source_urls,
                    matched_keywords=all_matched,
                    basic_info=profile_info or f"GitHub contributor: {repo}",
                    channel=channel,
                    lead_type=lead_type,
                    confidence=confidence,
                    score=score,
                    recommendation=recommendation(score),
                )
            else:
                candidates[key].source_urls |= source_urls
                candidates[key].phones |= phones
                candidates[key].matched_keywords = list(dict.fromkeys(candidates[key].matched_keywords + all_matched))
                if candidates[key].basic_info.startswith("GitHub contributor") and profile_info:
                    candidates[key].basic_info = profile_info
    return list(candidates.values())


def github_git_log_candidates(url: str, base_score: int, matched: list[str], commit_limit: int = 30) -> list[Candidate]:
    repo = github_repo_slug(url)
    if not repo:
        return []
    commit_limit = max(1, min(commit_limit, 100))
    clone_url = f"https://github.com/{repo}.git"
    temp_root = tempfile.mkdtemp(prefix="tech_candidate_git_")
    repo_dir = str(Path(temp_root) / "repo.git")
    try:
        subprocess.run(
            ["git", "clone", "--bare", "--depth", str(max(commit_limit, 30)), "--filter=blob:none", clone_url, repo_dir],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=True,
        )
        result = subprocess.run(
            ["git", f"--git-dir={repo_dir}", "log", "-n", str(commit_limit), "--format=%an%x00%ae%x00%H"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=True,
        )
    except Exception:
        return []
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    candidates: dict[str, Candidate] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\x00")
        if len(parts) != 3:
            continue
        name, email, commit_hash = [part.strip() for part in parts]
        emails = extract_emails(email)
        if not name or not emails:
            continue
        key = next(iter(sorted(emails))).lower()
        source_url = f"https://github.com/{repo}/commit/{commit_hash}"
        if key not in candidates:
            candidates[key] = Candidate(
                name=name,
                emails=emails,
                source_urls={source_url, f"https://github.com/{repo}"},
                matched_keywords=matched,
                basic_info=f"GitHub git-log contributor: {repo}",
                channel="github_commit",
                lead_type="repo_contributor",
                confidence="low",
                score=max(base_score, 60),
                recommendation=recommendation(max(base_score, 60)),
            )
        else:
            candidates[key].source_urls.add(source_url)
    return list(candidates.values())


def has_china_focus_signal(cand: Candidate) -> bool:
    haystack = " ".join(
        [
            cand.basic_info,
            cand.channel,
            cand.lead_type,
            " ".join(cand.source_urls),
            " ".join(cand.matched_keywords),
        ]
    ).lower()
    return has_china_public_signal(haystack)


def apply_focus_preference(candidates: Iterable[Candidate], focus: str) -> list[Candidate]:
    updated: list[Candidate] = []
    for cand in candidates:
        if focus == "china" and has_china_focus_signal(cand):
            cand.score = min(100, cand.score + 8)
            cand.recommendation = recommendation(cand.score)
            if "china_focus_public_signal" not in cand.matched_keywords:
                cand.matched_keywords.append("china_focus_public_signal")
            if "China-focused public source signal" not in cand.basic_info:
                cand.basic_info = f"{cand.basic_info}; China-focused public source signal"[:180]
        updated.append(cand)
    return updated


def merge_candidates(candidates: Iterable[Candidate]) -> list[Candidate]:
    groups: dict[int, Candidate] = {}
    email_to_group: dict[str, int] = {}
    name_to_group: dict[str, int] = {}
    next_group = 0
    confidence_rank = {"high": 3, "medium": 2, "low": 1}

    def combine(existing: Candidate, incoming: Candidate) -> Candidate:
        existing.emails |= incoming.emails
        existing.phones |= incoming.phones
        existing.source_urls |= incoming.source_urls
        existing.matched_keywords = list(dict.fromkeys(existing.matched_keywords + incoming.matched_keywords))
        if existing.basic_info == "见来源页" and incoming.basic_info != "见来源页":
            existing.basic_info = incoming.basic_info
        if confidence_rank.get(incoming.confidence, 0) > confidence_rank.get(existing.confidence, 0):
            existing.channel = incoming.channel
            existing.lead_type = incoming.lead_type
            existing.confidence = incoming.confidence
        existing.score = max(existing.score, incoming.score)
        existing.recommendation = recommendation(existing.score)
        return existing

    for cand in candidates:
        if is_likely_automation_identity(cand):
            continue
        normalized_emails = {email.lower() for email in cand.emails}
        matched_groups = {
            email_to_group[email]
            for email in normalized_emails
            if email in email_to_group and email_to_group[email] in groups
        }
        name_key = cand.name.lower()
        if not normalized_emails and name_key in name_to_group and name_to_group[name_key] in groups:
            matched_groups.add(name_to_group[name_key])
        if matched_groups:
            target = min(matched_groups)
            combined = groups[target]
            for other in sorted(matched_groups - {target}):
                combined = combine(combined, groups.pop(other))
                for email, group_id in list(email_to_group.items()):
                    if group_id == other:
                        email_to_group[email] = target
                for name, group_id in list(name_to_group.items()):
                    if group_id == other:
                        name_to_group[name] = target
            groups[target] = combine(combined, cand)
        else:
            target = next_group
            next_group += 1
            groups[target] = cand
        for email in {email.lower() for email in groups[target].emails}:
            email_to_group[email] = target
        name_to_group[groups[target].name.lower()] = target
        if cand.name:
            name_to_group[cand.name.lower()] = target
    return sorted(
        groups.values(),
        key=lambda c: (
            -confidence_rank.get(c.confidence, 0),
            c.channel in LOW_CONFIDENCE_CHANNELS,
            -c.score,
            c.name.lower(),
        ),
    )


def compact_join(values: Iterable[str], limit: int = 5) -> str:
    ordered = sorted(v for v in values if v)
    shown = ordered[:limit]
    extra = len(ordered) - len(shown)
    if extra > 0:
        shown.append(f"... +{extra} more")
    return "<br>".join(shown)


def candidate_research_direction(cand: Candidate) -> str:
    haystack = " ".join(cand.matched_keywords).lower()
    topic_labels = [
        (("database", "storage engine", "rocksdb", "tikv", "lsm"), "数据库与存储系统"),
        (("distributed systems", "distributed consensus", "raft", "paxos"), "分布式系统与一致性"),
        (("compiler", "programming languages"), "编译器与程序语言"),
        (("frontend", "react", "typescript"), "前端工程"),
        (("backend", "infrastructure", "kubernetes"), "后端与基础设施"),
        (("security", "privacy"), "系统安全与隐私"),
        (("computer vision",), "计算机视觉"),
        (("autoresearch", "ai scientist"), "自动化科研 / AI Scientist"),
        (("multi-agent", "multi agent"), "多智能体协作"),
        (("code agent", "program synthesis"), "代码智能体 / 程序合成"),
        (("tool use", "tool learning"), "工具学习与调用"),
        (("memory",), "智能体长期记忆"),
        (("reasoning",), "LLM 推理"),
        (("planning",), "规划、搜索与任务分解"),
        (("evaluation", "benchmark", "自动评测"), "Agent 评测与 Benchmark"),
        (("reinforcement learning",), "强化学习"),
        (("agent",), "LLM Agent"),
        (("llm",), "大语言模型"),
        (("nlp",), "自然语言处理"),
    ]
    labels = [label for tokens, label in topic_labels if any(token in haystack for token in tokens)]
    return "；".join(dict.fromkeys(labels)) or "需根据公开主页/论文进一步确认"


def candidate_public_evidence(cand: Candidate) -> str:
    source = compact_join(cand.source_urls, limit=3)
    matched = "、".join(cand.matched_keywords[:6]) or "公开页关键词不足"
    if "github" in cand.channel:
        return f"公开 GitHub 贡献/主页：{source}；相关方向：{matched}"
    return f"公开个人/高校主页：{source}；页面命中：{matched}"


def candidate_china_public_signal(cand: Candidate) -> str:
    evidence = china_public_evidence(
        " ".join([cand.basic_info, " ".join(cand.source_urls), " ".join(cand.matched_keywords)])
    )
    if evidence:
        return "；".join(evidence[:4])
    return "未发现明确的中国高校/机构/中文公开来源信号"


def render_table(candidates: list[Candidate]) -> str:
    headers = STANDARD_HEADERS
    rows = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for cand in candidates:
        urls = compact_join(cand.source_urls, limit=5)
        emails = compact_join(cand.emails, limit=4)
        phones = compact_join(cand.phones, limit=3) if cand.phones else "暂无"
        matched = "<br>".join(cand.matched_keywords) if cand.matched_keywords else "公开页关键词不足"
        if cand.channel == "github_commit":
            rec_points = "GitHub 公开仓库提交记录包含可触达邮箱；可作为项目贡献线索继续反查主页/论文/LinkedIn"
            risk = "低置信度：commit 作者不一定是目标岗位候选人；需确认身份、贡献深度、当前机构、求职意向和近期状态"
            action = "先反查主页/论文，再决定是否触达"
        elif cand.channel == "github_profile":
            rec_points = "GitHub 贡献记录已反查到公开个人资料/主页信息；可作为中等置信度候选人线索继续核验"
            risk = "仍需确认该 GitHub 身份和候选人真实履历、当前机构、求职意向、地点和薪资"
            action = "优先反查论文/主页后触达"
        else:
            rec_points = "公开主页/高校页包含 JD 关键词和可触达邮箱；适合作为候选人线索继续人工复核"
            risk = "邮箱已尽量按姓名/页面归属过滤；仍需确认本人 ownership、联系方式归属、求职意向、地点、薪资和近期状态"
            action = "优先电话/邮件确认" if cand.score >= 70 else "放入备选池"
        rows.append(
            "| "
            + " | ".join(
                md_escape(x)
                for x in [
                    cand.name,
                    cand.basic_info,
                    phones,
                    emails,
                    cand.channel,
                    cand.lead_type,
                    cand.confidence,
                    cand.recommendation,
                    f"{cand.score}/100",
                    urls,
                    matched,
                    rec_points,
                    risk,
                    action,
                    candidate_research_direction(cand),
                    candidate_public_evidence(cand),
                    candidate_china_public_signal(cand),
                ]
            )
            + " |"
        )
    return "\n".join(rows) + "\n"


def channel_summary(candidates: list[Candidate]) -> str:
    counts: dict[str, int] = {}
    for cand in candidates:
        counts[cand.channel] = counts.get(cand.channel, 0) + 1
    if not counts:
        return "无"
    return ", ".join(f"{channel}: {count}" for channel, count in sorted(counts.items()))


def main() -> int:
    global ACTIVE_DEADLINE, COLLECT_PHONES
    parser = argparse.ArgumentParser(description="Source public technical candidates from a JD.")
    parser.add_argument("--jd", required=True, help="Path to JD markdown/text file.")
    parser.add_argument("--out", default="outputs/tech_candidates.md", help="Output Markdown path.")
    parser.add_argument("--search-limit", type=int, default=2, help="Results to keep per query.")
    parser.add_argument("--max-urls", type=int, default=15, help="Maximum fetched URLs including seeds.")
    parser.add_argument("--depth", choices=["quick", "deep"], default="quick", help="Search breadth. Use deep for professor/student/lab queries.")
    parser.add_argument("--focus", choices=["general", "china"], default="general", help="Use china to prioritize China-affiliated or Chinese-language public sources without inferring protected identity.")
    parser.add_argument("--require-china-signal", action="store_true", help="Keep only candidates with explicit public China affiliation/location/domain/language evidence. This does not infer nationality or ethnicity.")
    parser.add_argument("--no-github-search", action="store_true", help="Disable GitHub repository search fallback.")
    parser.add_argument("--github-user-limit", type=int, default=8, help="Maximum public GitHub user profiles to inspect from keyword search.")
    parser.add_argument("--github-contributor-limit", type=int, default=20, help="Maximum public GitHub repo contributors to inspect per discovered repository.")
    parser.add_argument("--github-commit-limit", type=int, default=20, help="Maximum public GitHub commits to inspect per discovered repository.")
    parser.add_argument("--no-academic-expand", action="store_true", help="Disable academic people/lab page link expansion.")
    parser.add_argument("--academic-link-limit", type=int, default=30, help="Maximum profile links to inspect per academic people/lab page.")
    parser.add_argument("--seed-url", action="append", default=[], help="Public URL to evaluate before search. Repeatable.")
    parser.add_argument("--seed-file", action="append", default=[], help="Text file with one public URL per line. Repeatable.")
    parser.add_argument("--no-search", action="store_true", help="Only evaluate seed URLs/files; do not run search queries.")
    parser.add_argument("--collect-phones", action="store_true", help="Collect public phone-like strings. Disabled by default because false positives and sensitivity are higher than email.")
    parser.add_argument("--deadline-seconds", type=int, default=0, help="Internal graceful deadline. Writes partial output instead of losing all progress; 0 disables it.")
    args = parser.parse_args()

    COLLECT_PHONES = args.collect_phones
    ACTIVE_DEADLINE = time.monotonic() + args.deadline_seconds if args.deadline_seconds > 0 else 0.0

    jd_path = Path(args.jd)
    jd_text = jd_path.read_text(encoding="utf-8")
    keywords = jd_keywords(jd_text)
    urls = discover_urls(
        jd_text=jd_text,
        search_limit=args.search_limit,
        seed_urls=args.seed_url,
        seed_files=args.seed_file,
        no_search=args.no_search,
        max_urls=args.max_urls,
        depth=args.depth,
        github_search=not args.no_github_search,
        focus=args.focus,
    )

    candidates: list[Candidate] = []
    for url in urls:
        if deadline_reached():
            add_search_note("Internal deadline reached while fetching candidate pages; output is partial.")
            break
        candidates.extend(
            candidates_from_url(
                url,
                keywords,
                expand_academic=not args.no_academic_expand,
                academic_link_limit=args.academic_link_limit,
                github_contributor_limit=args.github_contributor_limit,
                github_commit_limit=args.github_commit_limit,
            )
        )
        time.sleep(0.2)

    if not deadline_reached() and not args.no_search and not args.no_github_search:
        candidates.extend(github_user_search_candidates(keywords, args.github_user_limit))

    merged = merge_candidates(apply_focus_preference(candidates, args.focus))
    if args.require_china_signal:
        merged = [candidate for candidate in merged if has_china_focus_signal(candidate)]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = [
        "# 通用技术岗公开候选人 MVP 输出",
        "",
        f"- JD: `{jd_path}`",
        f"- 关键词: {', '.join(keywords) if keywords else '未提取到关键词'}",
        f"- 检索/种子 URL 数: {len(urls)}",
        f"- Focus: `{args.focus}`",
        f"- Require China public signal: `{args.require_china_signal}` (public evidence only; no nationality/ethnicity inference)",
        f"- Phone collection: `{'enabled' if args.collect_phones else 'disabled'}`",
        f"- Partial result: `{'yes' if deadline_reached() else 'no'}`",
        f"- 公开邮箱候选人数: {len(merged)}",
        f"- 渠道分布: {channel_summary(merged)}",
        f"- Search diagnostics: {'; '.join(SEARCH_NOTES[:20]) if SEARCH_NOTES else 'none'}",
        "- 规则: 只记录公开网页中直接出现、常见公开混淆写法、公开 GitHub profile/contributor 信息或公开 commit metadata 中的邮箱；多邮箱页面会优先保留和姓名/页面路径匹配的邮箱；不猜测邮箱。",
        "",
        render_table(merged),
    ]
    out_path.write_text("\n".join(body), encoding="utf-8")
    print(f"Wrote {len(merged)} candidates to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
