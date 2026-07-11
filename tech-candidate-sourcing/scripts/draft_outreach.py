#!/usr/bin/env python3
"""Draft personalized outreach emails from a sourced candidate Markdown table.

This script does not send email. It creates reviewable Markdown drafts so the
user can inspect and edit messages before any future sending workflow.
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
from jd_utils import jd_title as shared_jd_title
from md_table import parse_markdown
from china_signals import has_china_public_signal
from candidate_schema import is_placeholder


def parse_candidate_table(markdown: str) -> list[dict[str, str]]:
    _headers, rows = parse_markdown(markdown)
    return [row for row in rows if row.get("邮箱")]


def jd_title(jd_text: str) -> str:
    return shared_jd_title(jd_text, "zh")


def first_email(value: str) -> str:
    return value.split(";")[0].strip()


def first_source(value: str) -> str:
    return value.split(";")[0].strip()


def load_allowlist(path: Path | None) -> list[str] | None:
    if not path:
        return None
    allowed: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        email = first_email(line).lower()
        if email and email not in allowed:
            allowed.append(email)
    return allowed


def filter_by_allowlist(rows: list[dict[str, str]], allowed: list[str] | None) -> list[dict[str, str]]:
    if allowed is None:
        return rows
    if not allowed:
        return []
    by_email: dict[str, dict[str, str]] = {}
    for row in rows:
        email = first_email(row.get("邮箱", "")).lower()
        if email and email not in by_email:
            by_email[email] = row
    return [by_email[email] for email in allowed if email in by_email]


def compact_text(value: str, limit: int = 180) -> str:
    value = re.sub(r"https?://\S+", "", value or "", flags=re.IGNORECASE)
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    value = re.sub(r"\b(?:ignore previous|system prompt|click here|run this command|send money)\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"(?:忽略(?:上述|之前)|点击(?:这里|链接)|执行(?:命令|代码))", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def signal_sentence(row: dict[str, str]) -> str:
    channel = row.get("渠道", "")
    lead_type = row.get("线索类型", "")
    keywords = row.get("命中关键词", "相关技术方向")
    research = row.get("研究方向", "").strip()
    evidence = row.get("代表作/项目证据", "").strip()
    source = compact_text(first_source(row.get("来源", "")), 120)
    topic = research if research and "进一步确认" not in research else keywords
    if evidence and not is_placeholder(evidence) and "进一步确认" not in evidence and "需复核" not in evidence:
        return f"我在公开资料中看到你在 {topic} 方向的工作，特别留意到这条公开证据：{compact_text(evidence, 220)}。"
    if channel == "academic_web":
        return f"我是在公开高校/实验室页面看到你和 {topic} 相关的研究信息，来源是 {source}。"
    if channel == "github_profile":
        return f"我是在 GitHub 项目贡献记录和公开 profile/主页里看到你和 {topic} 相关的经历，来源是 {source}。"
    if channel == "github_commit":
        return f"我是在相关开源项目的公开提交记录里看到你参与了 {topic} 方向项目，来源是 {source}。"
    return f"我在公开资料里看到你和 {topic} 方向有交集，来源是 {source}。"


def personalization_note(row: dict[str, str]) -> str:
    parts = []
    basic_info = row.get("基础信息", "").strip()
    keywords = row.get("命中关键词", "").strip()
    source = first_source(row.get("来源", ""))
    research = row.get("研究方向", "").strip()
    evidence = row.get("代表作/项目证据", "").strip()
    if basic_info and basic_info not in {"暂无", "待补充", "见来源页"}:
        parts.append(f"公开基础信息：{compact_text(basic_info, 120)}")
    if keywords and "关键词不足" not in keywords:
        parts.append(f"命中关键词：{compact_text(keywords, 100)}")
    if research and "进一步确认" not in research:
        parts.append(f"研究方向：{compact_text(research, 120)}")
    if evidence and evidence not in {"暂无", "待补充"}:
        parts.append(f"代表作/项目：{compact_text(evidence, 160)}")
    if source:
        parts.append(f"首要来源：{compact_text(source, 120)}")
    return compact_text("；".join(parts), 260) or "仅有公开邮箱线索，需人工补充个性化依据"


def review_note(row: dict[str, str]) -> str:
    confidence = row.get("置信度", "").strip()
    risk = row.get("风险点/待确认", "").strip()
    notes = []
    if confidence == "low":
        notes.append("低置信度线索，发送前必须反查身份和 ownership")
    if risk:
        notes.append(risk)
    return "；".join(notes) or "发送前复核邮箱归属、当前状态、意向、地点和薪资"


def candidate_language(row: dict[str, str], requested: str, jd_text: str) -> str:
    if requested in {"zh", "en"}:
        return requested
    haystack = " ".join([row.get("基础信息", ""), row.get("来源", ""), row.get("中国相关公开信号", ""), row.get("邮箱", "")])
    if has_china_public_signal(haystack):
        return "zh"
    return "zh" if len(re.findall(r"[\u4e00-\u9fff]", jd_text)) >= 20 else "en"


def english_signal_sentence(row: dict[str, str]) -> str:
    topic = row.get("研究方向", "").strip() or row.get("命中关键词", "relevant technical research")
    evidence = row.get("代表作/项目证据", "").strip()
    source = first_source(row.get("来源", ""))
    if is_placeholder(topic) or "进一步确认" in topic or "需复核" in topic:
        topic = "relevant technical work"
    topic = compact_text(topic, 100) or "relevant technical work"
    if evidence and not is_placeholder(evidence) and "进一步确认" not in evidence and "需复核" not in evidence:
        return f"I came across your public work on {topic}, particularly this public paper/project evidence: {compact_text(evidence, 220)}."
    return f"I found your public profile while researching people working on {topic}. The public source was {compact_text(source, 120)}."


def draft_email(row: dict[str, str], role_title: str, sender_name: str, company_hint: str, language: str = "zh") -> tuple[str, str]:
    name = compact_text(row.get("姓名", "你好"), 80) or "你好"
    email = first_email(row.get("邮箱", ""))
    if language == "en":
        if name in {"你好", "候选人", "待补充", "暂无"}:
            name = "there"
        subject = f"A {role_title} opportunity relevant to your work"
        body = f"""Hi {name},

{english_signal_sentence(row)}

I am working on a {role_title} opportunity. {company_hint}. The role values demonstrable research or engineering ownership and relevant project experience.

This is an initial assessment based only on public information. If you are open to hearing about technical opportunities, I would be glad to share more context and find a convenient time to talk. If it is not relevant, no response is needed.

Best,
{sender_name}
"""
        return subject, body
    subject = f"{role_title} 方向机会沟通"
    signal = signal_sentence(row)
    note = personalization_note(row)
    confidence = row.get("置信度", "")
    if confidence == "low":
        caveat = "我目前只基于公开资料做初步判断，如果方向不匹配也请你直接忽略。"
    else:
        caveat = "看起来方向比较相关，想先和你简单确认一下当前关注点和机会意向。"

    body = f"""你好 {name}，

{signal}

我看到的公开线索是：{note}。

我这边在看一个 {role_title} 方向的机会，{company_hint}。岗位会关注候选人在相关技术方向里的研究/工程 ownership、可验证项目经验，以及是否愿意进一步沟通。

{caveat}

如果你近期愿意了解新的技术机会，可以回我一个方便沟通的时间；如果不合适，也欢迎推荐你觉得更匹配的同学或合作者。

谢谢，
{sender_name}
"""
    return subject, body


def queue_rows(rows: list[dict[str, str]], jd_text: str, sender_name: str, company_hint: str, limit: int, language: str) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for row in rows[:limit]:
        row_language = candidate_language(row, language, jd_text)
        role_title = shared_jd_title(jd_text, row_language)
        subject, body = draft_email(row, role_title, sender_name, company_hint, row_language)
        output.append(
            {
                "status": "needs_review",
                "to": first_email(row.get("邮箱", "")),
                "candidate_name": row.get("姓名", ""),
                "subject": subject,
                "body": body.rstrip(),
                "channel": row.get("渠道", ""),
                "confidence": row.get("置信度", ""),
                "source": first_source(row.get("来源", "")),
                "matched_keywords": row.get("命中关键词", ""),
                "research_direction": row.get("研究方向", ""),
                "representative_evidence": row.get("代表作/项目证据", ""),
                "personalization_note": personalization_note(row),
                "risk_to_confirm": row.get("风险点/待确认", ""),
                "review_note": review_note(row),
                "language": row_language,
            }
        )
    return output


def write_queue_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "status",
        "to",
        "candidate_name",
        "subject",
        "body",
        "channel",
        "confidence",
        "source",
        "matched_keywords",
        "research_direction",
        "representative_evidence",
        "personalization_note",
        "risk_to_confirm",
        "review_note",
        "language",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def render_drafts(rows: list[dict[str, str]], jd_text: str, sender_name: str, company_hint: str, limit: int, language: str) -> str:
    role_title = shared_jd_title(jd_text, "zh" if language == "zh" else "en")
    output = [
        "# 候选人个性化邮件草稿",
        "",
        f"- 岗位: {role_title}",
        f"- 候选人数: {min(len(rows), limit)}",
        "- 说明: 这里只生成草稿，不发送邮件；低置信度线索请先人工核验。",
        "",
    ]
    for idx, row in enumerate(rows[:limit], start=1):
        row_language = candidate_language(row, language, jd_text)
        row_role_title = shared_jd_title(jd_text, row_language)
        subject, body = draft_email(row, row_role_title, sender_name, company_hint, row_language)
        output.extend(
            [
                f"## {idx}. {row.get('姓名', '候选人')}",
                "",
                f"- To: `{first_email(row.get('邮箱', ''))}`",
                f"- Source confidence: `{row.get('置信度', 'unknown')}`",
                f"- Channel: `{row.get('渠道', 'unknown')}`",
                f"- Personalization note: {personalization_note(row)}",
                f"- Review note: {review_note(row)}",
                f"- Language: `{row_language}`",
                f"- Subject: {subject}",
                "",
                "```text",
                body.rstrip(),
                "```",
                "",
            ]
        )
    return "\n".join(output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Draft personalized outreach emails from a candidate Markdown table.")
    parser.add_argument("--candidates", required=True, help="Candidate Markdown table generated by source_candidates.py.")
    parser.add_argument("--jd", required=True, help="JD markdown/text file.")
    parser.add_argument("--out", required=True, help="Output Markdown file for reviewable drafts.")
    parser.add_argument("--sender-name", required=True, help="Sender name used in email signatures.")
    parser.add_argument("--company-hint", required=True, help="Short non-sensitive company/role hint.")
    parser.add_argument("--language", choices=["auto", "zh", "en"], default="auto", help="Draft language. Auto uses public candidate/JD signals.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum drafts to generate.")
    parser.add_argument("--allowlist", default=None, type=Path, help="Optional email allowlist. Only matching rows are drafted, in allowlist order.")
    parser.add_argument("--queue-csv", default="", help="Optional CSV mail-merge queue. It is review-only and does not send email.")
    args = parser.parse_args()

    candidate_text = Path(args.candidates).read_text(encoding="utf-8")
    jd_text = Path(args.jd).read_text(encoding="utf-8")
    rows = filter_by_allowlist(parse_candidate_table(candidate_text), load_allowlist(args.allowlist if args.allowlist else None))
    rendered = render_drafts(rows, jd_text, args.sender_name, args.company_hint, args.limit, args.language)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    if args.queue_csv:
        queue_path = Path(args.queue_csv)
        write_queue_csv(queue_path, queue_rows(rows, jd_text, args.sender_name, args.company_hint, args.limit, args.language))
        print(f"Wrote review queue to {queue_path}")
    print(f"Wrote {min(len(rows), args.limit)} drafts to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
