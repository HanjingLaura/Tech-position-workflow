#!/usr/bin/env python3
"""Conservative public China-related evidence detection with token boundaries."""

from __future__ import annotations

import re


NEGATIVE_SIGNAL_RE = re.compile(
    r"未发现明确的中国[^;；\n]*|no explicit china[^;\n]*|needs manual review[^;\n]*",
    re.IGNORECASE,
)

DOMAIN_PATTERNS = [
    (re.compile(r"(?i)(?:@|https?://[^/]*\.)[A-Za-z0-9.-]*edu\.cn\b"), "中国高校域名"),
    (re.compile(r"(?i)(?:@|https?://[^/]*\.)[A-Za-z0-9.-]*ac\.cn\b"), "中国科研机构域名"),
    (re.compile(r"(?i)@[A-Za-z0-9.-]+\.cn\b"), "中国机构邮箱域名"),
    (re.compile(r"(?i)https?://[^/]+\.cn(?:/|$)"), "中国大陆公开网页域名"),
    (re.compile(r"(?i)(?:@|https?://[^/]*\.)[A-Za-z0-9.-]*\.hk\b"), "香港高校/机构域名"),
]

TOKEN_PATTERNS = [
    (re.compile(r"(?i)\b(?:tsinghua|peking university|zhejiang university|shanghai jiao tong|fudan university|renmin university|beihang university|chinese academy of sciences)\b"), "中国高校/科研机构公开关联"),
    (re.compile(r"(?i)\b(?:pku|sjtu|zju|ustc)\b"), "中国高校/科研机构缩写公开关联"),
    (re.compile(r"(?i)\b(?:alibaba|tencent|bytedance|baidu|huawei)\b"), "中国科技机构公开关联"),
    (re.compile(r"(?i)\b(?:beijing|shanghai|shenzhen|hangzhou|guangzhou|nanjing|hong kong|china)\b|\(hk\)"), "中国地区/所在地公开关联"),
    (re.compile(r"清华|北京大学|北大|上海交通大学|上海交大|浙江大学|中国科学技术大学|中科大|复旦|中国科学院|中科院|人民大学|北京航空航天大学"), "中国高校/科研机构中文公开关联"),
    (re.compile(r"阿里巴巴|阿里|腾讯|字节跳动|字节|百度|华为"), "中国科技机构中文公开关联"),
    (re.compile(r"中国|北京|上海|深圳|杭州|广州|南京|香港"), "中国地区中文公开关联"),
]


def china_public_evidence(text: str) -> list[str]:
    cleaned = NEGATIVE_SIGNAL_RE.sub(" ", text or "")
    labels: list[str] = []
    for pattern, label in DOMAIN_PATTERNS + TOKEN_PATTERNS:
        if pattern.search(cleaned) and label not in labels:
            labels.append(label)
    return labels


def has_china_public_signal(text: str) -> bool:
    return bool(china_public_evidence(text))
