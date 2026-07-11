#!/usr/bin/env python3
"""Shared Markdown-table parsing and rendering helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path


def split_md_row(line: str) -> list[str]:
    body = line.strip().strip("|")
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in body:
        if escaped:
            if char in {"\\", "|"}:
                current.append(char)
            else:
                current.extend(["\\", char])
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    return cells


def clean_cell(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("<br>", "; ")).strip()


def parse_markdown(markdown: str) -> tuple[list[str], list[dict[str, str]]]:
    table_lines = [line.strip() for line in markdown.splitlines() if line.strip().startswith("|") and line.strip().endswith("|")]
    if not table_lines:
        return [], []
    headers = [clean_cell(cell) for cell in split_md_row(table_lines[0])]
    rows: list[dict[str, str]] = []
    for line_number, line in enumerate(table_lines[1:], start=2):
        cells = [clean_cell(cell) for cell in split_md_row(line)]
        if cells and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
            continue
        if len(cells) != len(headers):
            raise ValueError(
                f"Malformed Markdown table row {line_number}: expected {len(headers)} cells, got {len(cells)}"
            )
        rows.append(dict(zip(headers, cells)))
    return headers, rows


def parse_path(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    return parse_markdown(path.read_text(encoding="utf-8"))


def escape_md(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value or "").strip()
    return normalized.replace("\\", "\\\\").replace("|", "\\|")


def render_rows(headers: list[str], rows: list[dict[str, str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    lines.extend("| " + " | ".join(escape_md(row.get(header, "")) for header in headers) + " |" for row in rows)
    return "\n".join(lines) + "\n"


def write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append({str(k): str(v or "") for k, v in json.loads(line).items()})
    return rows
