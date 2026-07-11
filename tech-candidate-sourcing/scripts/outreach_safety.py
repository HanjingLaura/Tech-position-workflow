#!/usr/bin/env python3
"""Shared outbound-copy safety checks."""

from __future__ import annotations

import re


PLACEHOLDER_RE = re.compile(
    r"(?:<|&lt;)\s*[A-Z][A-Z0-9_ -]{2,}\s*(?:>|&gt;)|"
    r"\{\{\s*[A-Z][A-Z0-9_ -]{2,}\s*\}\}"
)
UNSAFE_BODY_RE = re.compile(
    r"https?://|\b(?:ignore previous|system prompt|click here|run this command|send money)\b|"
    r"(?:忽略(?:上述|之前)|点击(?:这里|链接)|执行(?:命令|代码))",
    re.IGNORECASE,
)


def has_unresolved_placeholder(subject: str, body: str) -> bool:
    return bool(PLACEHOLDER_RE.search(f"{subject}\n{body}"))


def has_unsafe_public_content(*parts: str) -> bool:
    return bool(UNSAFE_BODY_RE.search("\n".join(part or "" for part in parts)))
