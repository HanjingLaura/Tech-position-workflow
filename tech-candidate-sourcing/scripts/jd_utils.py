#!/usr/bin/env python3
"""Standard-library-only JD keyword and title helpers."""

from __future__ import annotations

import re


TERM_ALIASES: list[tuple[tuple[str, ...], str]] = [
    (("rocksdb",), "RocksDB"),
    (("tikv",), "TiKV"),
    (("lsm-tree", "lsm tree", "lsm树"), "LSM tree"),
    (("transaction processing", "transaction system", "事务系统", "事务处理"), "transaction processing"),
    (("distributed consensus", "consensus protocol", "分布式一致性", "共识协议"), "distributed consensus"),
    (("storage engine", "存储引擎"), "storage engine"),
    (("database kernel", "数据库内核"), "database kernel"),
    (("distributed systems", "distributed system", "分布式系统"), "distributed systems"),
    (("query optimizer", "query optimization", "查询优化"), "query optimization"),
    (("database", "数据库"), "database systems"),
    (("machine learning", "机器学习"), "machine learning"),
    (("deep learning", "深度学习"), "deep learning"),
    (("large language model", "大语言模型", "大模型"), "large language models"),
    (("multi-agent", "multi agent", "多智能体"), "multi-agent systems"),
    (("tool use", "tool calling", "工具调用"), "tool use"),
    (("reasoning", "推理"), "reasoning"),
    (("planning", "规划"), "planning"),
    (("reinforcement learning", "强化学习"), "reinforcement learning"),
    (("agent", "智能体"), "LLM agents"),
    (("natural language processing", "nlp", "自然语言处理"), "natural language processing"),
    (("computer vision", "计算机视觉"), "computer vision"),
    (("compiler", "编译器"), "compilers"),
    (("programming language", "程序语言"), "programming languages"),
    (("frontend", "前端"), "frontend engineering"),
    (("backend", "后端"), "backend systems"),
    (("security", "安全"), "computer security"),
    (("data engineering", "数据工程"), "data engineering"),
    (("recommendation system", "推荐系统"), "recommender systems"),
    (("robotics", "机器人"), "robotics"),
]

TECH_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:RocksDB|TiKV|LevelDB|Paxos|Raft|Spanner|CockroachDB|PostgreSQL|MySQL|Redis|"
    r"Kubernetes|PyTorch|TensorFlow|TypeScript|React|Rust|C\+\+|CUDA)(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def jd_keywords(jd_text: str, max_keywords: int = 6) -> list[str]:
    lowered = jd_text.lower()
    scored: dict[str, int] = {}
    for aliases, canonical in TERM_ALIASES:
        count = sum(lowered.count(alias.lower()) for alias in aliases)
        if count:
            specificity = max(len(alias) for alias in aliases)
            scored[canonical] = max(scored.get(canonical, 0), count * 10 + min(specificity, 30))

    for token in TECH_TOKEN_RE.findall(jd_text):
        canonical = next((name for aliases, name in TERM_ALIASES if token.lower() in {alias.lower() for alias in aliases}), token)
        scored[canonical] = max(scored.get(canonical, 0), 35 + lowered.count(token.lower()) * 5)

    ranked = sorted(scored.items(), key=lambda item: (-item[1], item[0].lower()))
    return [term for term, _ in ranked[:max_keywords]]


def jd_title(jd_text: str, language: str = "auto") -> str:
    for line in jd_text.splitlines():
        stripped = line.strip(" #\t")
        if not stripped or len(stripped) > 80:
            continue
        if re.search(r"岗位|职位|工程师|研究员|算法|developer|engineer|researcher|scientist|architect|role|position", stripped, re.I):
            return re.sub(r"\s*JD\s*$", "", stripped, flags=re.I).strip()
    for line in jd_text.splitlines():
        stripped = line.strip(" #\t")
        if stripped and len(stripped) <= 80:
            return stripped
    return "技术岗位" if language == "zh" else "Technical role"
