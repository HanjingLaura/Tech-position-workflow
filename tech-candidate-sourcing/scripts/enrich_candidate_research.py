#!/usr/bin/env python3
"""Add research direction and publication/project evidence to a candidate table."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from md_table import clean_cell, escape_md, parse_path
from china_signals import china_public_evidence


EXTRA_HEADERS = ["研究方向", "代表作/项目证据", "中国相关公开信号"]

TOPIC_LABELS = [
    (("database", "database kernel", "storage engine", "数据库", "存储引擎"), "数据库与存储系统"),
    (("distributed system", "infrastructure", "kubernetes", "cloud", "分布式", "基础设施"), "分布式系统与基础设施"),
    (("compiler", "programming language", "编译器", "程序语言"), "编译器与程序语言"),
    (("frontend", "react", "typescript", "web performance", "前端"), "前端工程与 Web 性能"),
    (("backend", "microservice", "server", "后端"), "后端与服务系统"),
    (("security", "privacy", "安全", "隐私"), "系统安全与隐私"),
    (("computer vision", "vision", "image", "视觉"), "计算机视觉"),
    (("robotics", "robot", "机器人"), "机器人学"),
    (("recommendation", "recommender", "推荐系统"), "推荐系统"),
    (("data engineering", "data platform", "数据工程", "数据平台"), "数据工程与平台"),
    (("autoresearch", "auto research", "ai scientist", "automated research"), "自动化科研 / AI Scientist"),
    (("multi-agent", "multi agent", "multiagent", "多智能体"), "多智能体协作"),
    (("code agent", "coding agent", "code intelligence", "程序合成"), "代码智能体 / 程序合成"),
    (("tool use", "tool-use", "tool learning", "tool calling", "工具调用"), "工具学习与调用"),
    (("long-term memory", "long term memory", "agent memory", "长期记忆"), "智能体长期记忆"),
    (("reasoning", "推理"), "LLM 推理"),
    (("planning", "search", "task decomposition", "任务分解", "规划"), "规划、搜索与任务分解"),
    (("evaluation", "benchmark", "评测", "自动评测"), "Agent 评测与 Benchmark"),
    (("reinforcement learning", "rl", "强化学习"), "强化学习"),
    (("agent", "智能体"), "LLM Agent"),
    (("large language model", "llm", "语言模型"), "大语言模型"),
    (("nlp", "natural language processing", "自然语言处理"), "自然语言处理"),
    (("pytorch", "python"), "机器学习系统与原型开发"),
]

def parse_table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    return parse_path(path)


def normalized_name(value: str) -> str:
    value = re.sub(r"\s+\d{4}$", "", value.strip())
    return "".join(char.lower() for char in value if char.isalnum())


def publication_evidence(path: Path | None) -> dict[str, dict[str, list[str]]]:
    if not path or not path.exists():
        return {}
    _, rows = parse_table(path)
    authors: dict[str, dict[str, list[str]]] = {}
    for row in rows:
        author = row.get("Author", "")
        key = normalized_name(author)
        title = row.get("Paper", "")
        if not key or not title:
            continue
        entry = row.get("Entry", "")
        paper_url_match = re.search(r"\[paper\]\(([^)]+)\)", entry)
        paper_url = paper_url_match.group(1) if paper_url_match else ""
        source = row.get("Discovery source", "") or row.get("Source", "")
        venue = row.get("Venue", "")
        topics = row.get("Research topics", "")
        evidence = title
        context = " / ".join(part for part in [source, venue] if part)
        if context:
            evidence += f" ({context})"
        if paper_url:
            evidence += f" {paper_url}"
        bucket = authors.setdefault(key, {"evidence": [], "topics": [], "affiliations": []})
        if evidence not in bucket["evidence"]:
            bucket["evidence"].append(evidence)
        for topic in re.split(r";|,|<br>", topics):
            topic = clean_cell(topic)
            if topic and topic not in bucket["topics"]:
                bucket["topics"].append(topic)
        affiliation = row.get("Public affiliation", "")
        if affiliation and affiliation not in bucket["affiliations"]:
            bucket["affiliations"].append(affiliation)
    return authors


def infer_research_direction(row: dict[str, str], paper_topics: list[str]) -> str:
    haystack = " ".join(
        [
            row.get("命中关键词", ""),
            row.get("基础信息", ""),
            row.get("推荐点", ""),
            row.get("代表作/项目证据", ""),
            " ".join(paper_topics),
        ]
    ).lower()
    labels: list[str] = []
    for tokens, label in TOPIC_LABELS:
        if any(token.lower() in haystack for token in tokens) and label not in labels:
            labels.append(label)
    for topic in paper_topics:
        lowered_topic = topic.lower()
        covered = (
            (lowered_topic == "agent" and any("Agent" in label for label in labels))
            or (lowered_topic == "llm" and "大语言模型" in labels)
            or (lowered_topic in {"multi-agent", "multi agent"} and "多智能体协作" in labels)
            or (lowered_topic == "reasoning" and "LLM 推理" in labels)
            or (lowered_topic == "planning" and "规划、搜索与任务分解" in labels)
            or (lowered_topic in {"tool use", "tool-use"} and "工具学习与调用" in labels)
        )
        if topic and topic not in labels and not covered:
            labels.append(topic)
    return "；".join(labels[:6]) or "需根据公开主页/论文进一步确认"


def infer_project_evidence(row: dict[str, str], papers: list[str]) -> str:
    if papers:
        return "；".join(papers[:3])
    source = row.get("来源", "")
    channel = row.get("渠道", "")
    keywords = row.get("命中关键词", "")
    if "github" in channel.lower():
        return f"公开 GitHub 贡献/主页：{source}；相关方向：{keywords}"
    return f"公开个人/高校主页：{source}；页面命中：{keywords}"


def infer_china_signal(row: dict[str, str]) -> str:
    signals = china_public_evidence(" ".join(row.values()))
    return "；".join(signals[:4]) or "未发现明确的中国高校/机构/中文公开来源信号"


def enrich_rows(rows: list[dict[str, str]], publications: dict[str, dict[str, list[str]]]) -> list[dict[str, str]]:
    enriched: list[dict[str, str]] = []
    for row in rows:
        item = dict(row)
        publication = publications.get(
            normalized_name(row.get("姓名", "")),
            {"evidence": [], "topics": [], "affiliations": []},
        )
        topics = publication.get("topics", [])
        papers = publication.get("evidence", [])
        affiliations = publication.get("affiliations", [])
        if affiliations:
            current_info = item.get("基础信息", "")
            richer_info = "; ".join(affiliations[:2])
            if richer_info.lower() not in current_info.lower():
                item["基础信息"] = f"{current_info}; {richer_info}" if current_info else richer_info
        inferred_direction = infer_research_direction(item, topics)
        current_direction = item.get("研究方向", "")
        generic_directions = {"agent", "llm", "multi-agent", "multi agent", "reasoning", "planning", "tool use", "memory", "evaluation", "nlp"}
        current_parts = [part.strip().lower() for part in re.split(r";|；|,", current_direction) if part.strip()]
        if (
            not current_direction
            or current_direction in {"待补充", "论文主题需复核", "需根据公开主页/论文进一步确认"}
            or (current_parts and all(part in generic_directions for part in current_parts))
        ):
            item["研究方向"] = inferred_direction
        elif inferred_direction not in current_direction and "进一步确认" not in inferred_direction:
            item["研究方向"] = f"{current_direction}；{inferred_direction}"

        current_evidence = item.get("代表作/项目证据", "")
        if papers or not current_evidence or current_evidence in {"待补充", "暂无"}:
            item["代表作/项目证据"] = infer_project_evidence(item, papers)

        current_china_signal = item.get("中国相关公开信号", "")
        inferred_china_signal = infer_china_signal(item)
        if not current_china_signal or current_china_signal in {"待补充", "暂无"}:
            item["中国相关公开信号"] = inferred_china_signal
        elif current_china_signal.startswith("未发现") and not inferred_china_signal.startswith("未发现"):
            item["中国相关公开信号"] = inferred_china_signal
        if papers:
            channels = [part.strip() for part in re.split(r";|<br>", item.get("渠道", "")) if part.strip()]
            if "paper_author" not in channels:
                channels.append("paper_author")
            item["渠道"] = "; ".join(channels)
        enriched.append(item)
    return enriched


def render(headers: list[str], rows: list[dict[str, str]], source: Path, publication_path: Path | None) -> str:
    final_headers = [header for header in headers if header not in EXTRA_HEADERS] + EXTRA_HEADERS
    lines = [
        "# 研究证据增强候选人表",
        "",
        f"- Source table: `{source}`",
        f"- Publication evidence: `{publication_path}`" if publication_path else "- Publication evidence: none",
        f"- Candidate rows: {len(rows)}",
        "- 中国相关公开信号只记录公开高校、机构、地区或中文主页证据，不推断族裔或国籍。",
        "",
        "| " + " | ".join(final_headers) + " |",
        "| " + " | ".join(["---"] * len(final_headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(escape_md(row.get(header, "")) for header in final_headers) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Add research and publication/project evidence to a candidate table.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--publication-queries", default=None, type=Path)
    args = parser.parse_args()

    headers, rows = parse_table(args.input)
    publication_path = args.publication_queries if args.publication_queries else None
    evidence = publication_evidence(publication_path)
    enriched = enrich_rows(rows, evidence)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(headers, enriched, args.input, publication_path), encoding="utf-8")
    matched = sum(1 for row in enriched if "paper_author" in row.get("渠道", ""))
    print(f"Enriched {len(enriched)} candidates; matched publication evidence for {matched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
