#!/usr/bin/env python3
"""Shared candidate schema, email parsing, and placeholder checks."""

from __future__ import annotations

import re


STANDARD_HEADERS = [
    "姓名", "基础信息", "电话", "邮箱", "渠道", "线索类型", "置信度", "推荐级别", "匹配分",
    "来源", "命中关键词", "推荐点", "风险点/待确认", "建议动作", "研究方向", "代表作/项目证据",
    "中国相关公开信号",
]

EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])"
    r"[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}"
    r"(?![A-Za-z0-9.-])"
)

PLACEHOLDERS = {
    "", "暂无", "待补充", "见来源页", "未评分", "未评到", "公开页关键词不足",
    "needs review", "unknown", "n/a",
}


def emails_from(value: str) -> list[str]:
    seen: list[str] = []
    for email in EMAIL_RE.findall(value or ""):
        lowered = email.lower()
        if lowered not in seen:
            seen.append(lowered)
    return seen


def is_placeholder(value: str) -> bool:
    return (value or "").strip().lower() in PLACEHOLDERS
