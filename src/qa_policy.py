"""P2.1 分级审校：给每个块打风险分，决定是否跑 QA。

风险维度：数字、专名（连续大写开头词）、长句、引号/破折号、生僻标点、长度。
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class RiskResult:
    score: float
    reasons: list[str]


def assess(text: str) -> RiskResult:
    reasons: list[str] = []
    score = 0.0

    numbers = len(re.findall(r"\d", text))
    if numbers:
        score += min(0.3, numbers * 0.02)
        reasons.append(f"数字 {numbers} 处")

    proper = len(re.findall(r"\b[A-Z][a-z]{2,}", text))
    if proper >= 3:
        score += min(0.25, proper * 0.01)
        reasons.append(f"专名 {proper} 个")

    sentences = [s for s in re.split(r"(?<=[.!?。！？])", text) if s.strip()]
    long_sent = sum(1 for s in sentences if len(s.split()) > 28)
    if long_sent:
        score += min(0.25, long_sent * 0.05)
        reasons.append(f"长句 {long_sent} 句")

    quotes = text.count('"') + text.count("“") + text.count("”")
    if quotes >= 4:
        score += 0.1
        reasons.append("对话/引号密集")

    if "—" in text or "--" in text:
        score += 0.1
        reasons.append("含破折号")

    if len(text) > 900:
        score += 0.05
        reasons.append("块较长")

    return RiskResult(min(1.0, round(score, 3)), reasons)


def should_review(
    text: str,
    mode: str = "always",
    threshold: float = 0.35,
    sample_rate: float = 0.25,
    index: int = 0,
) -> tuple[bool, RiskResult]:
    """返回 (是否审校, 风险详情)。mode: always / risk / sample / off"""
    risk = assess(text)
    if mode == "off":
        return False, risk
    if mode == "always":
        return True, risk
    if mode == "risk":
        return risk.score >= threshold, risk
    if mode == "sample":
        return (index % max(1, int(1 / max(0.01, sample_rate)))) == 0 or risk.score >= threshold, risk
    return True, risk
