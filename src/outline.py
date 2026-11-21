"""P1.4 章节摘要：把长文按章节切开，逐节生成一句摘要，翻译时作为上下文注入。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class Section:
    title: str
    body: str
    start: int
    end: int


_HEADING = re.compile(r"^(\s*)(第[一二三四五六七八九十百]+[章节部分]|[IVX]+\.|\d+[.、)]|[A-Z][A-Z0-9 ,'\-]{4,80})$")


def split_sections(text: str, min_chars: int = 1200, target_chars: int = 4000) -> list[Section]:
    lines = text.splitlines(keepends=True)
    sections: list[Section] = []
    offset = 0
    cur_title = ""
    cur_start = 0
    buf: list[str] = []

    def flush(end: int) -> None:
        nonlocal buf, cur_start, cur_title
        body = "".join(buf).strip()
        if body:
            sections.append(Section(cur_title or f"第 {len(sections) + 1} 节", body, cur_start, end))
        buf = []

    for line in lines:
        stripped = line.strip()
        is_heading = bool(stripped) and len(stripped) <= 90 and bool(_HEADING.match(stripped))
        if is_heading and len("".join(buf)) >= min_chars:
            flush(offset)
            cur_title = stripped
            cur_start = offset
        buf.append(line)
        offset += len(line)

    flush(offset)

    # 没有明显标题时按长度硬切
    if len(sections) <= 1 and len(text) > target_chars:
        sections = []
        pos = 0
        idx = 1
        while pos < len(text):
            end = min(len(text), pos + target_chars)
            sections.append(Section(f"第 {idx} 节", text[pos:end], pos, end))
            pos = end
            idx += 1
    return sections


class SectionSummarizer:
    def __init__(self, client: Any, model: str = "") -> None:
        self.client = client
        self.model = model

    def summarize(self, sections: list[Section], limit: int = 12) -> list[str]:
        out: list[str] = []
        for sec in sections[:limit]:
            snippet = sec.body[:1800]
            try:
                res = self.client.chat(
                    [
                        {"role": "system", "content": "你是翻译前的结构分析助手，只输出一句中文摘要，不超过 60 字。"},
                        {"role": "user", "content": f"本节标题：{sec.title}\n正文：\n{snippet}\n\n请用一句中文概括本节讲什么、有哪些需要注意的术语或风格。"},
                    ],
                    temperature=0.1,
                    max_tokens=160,
                )
                out.append((res.text or "").strip().replace("\n", " ")[:120])
            except Exception:
                out.append("")
        return out


def chunk_section_index(chunks: list[str], text: str, sections: list[Section]) -> list[int]:
    """给每个块找到所属章节下标（按原文位置）。"""
    if not sections:
        return [0] * len(chunks)
    result: list[int] = []
    cursor = 0
    for chunk in chunks:
        pos = text.find(chunk[:40], cursor)
        if pos < 0:
            pos = cursor
        cursor = pos
        idx = 0
        for i, sec in enumerate(sections):
            if sec.start <= pos < sec.end:
                idx = i
                break
        result.append(idx)
    return result
