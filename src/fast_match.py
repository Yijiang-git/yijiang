"""纯 Python 的 Aho–Corasick 多模式匹配，用于术语表快速召回。

不依赖第三方库，桌面与打包环境都能直接用。
构建 O(总字符数)，匹配 O(文本长度 + 命中数)。
"""
from __future__ import annotations

from collections import deque
from typing import Iterable, Iterator


class AhoCorasick:
    def __init__(self, case_insensitive: bool = True) -> None:
        self.case_insensitive = case_insensitive
        self._next: list[dict[str, int]] = [{}]
        self._fail: list[int] = [0]
        self._out: list[list[int]] = [[]]
        self._patterns: list[str] = []

    def _norm(self, s: str) -> str:
        return s.lower() if self.case_insensitive else s

    def add(self, pattern: str) -> int:
        if not pattern:
            return -1
        p = self._norm(pattern)
        node = 0
        for ch in p:
            nxt = self._next[node].get(ch)
            if nxt is None:
                self._next.append({})
                self._fail.append(0)
                self._out.append([])
                nxt = len(self._next) - 1
                self._next[node][ch] = nxt
            node = nxt
        idx = len(self._patterns)
        self._patterns.append(pattern)
        self._out[node].append(idx)
        return idx

    def build(self) -> None:
        q: deque[int] = deque()
        for child in self._next[0].values():
            self._fail[child] = 0
            q.append(child)
        while q:
            node = q.popleft()
            for ch, child in self._next[node].items():
                f = self._fail[node]
                while f and ch not in self._next[f]:
                    f = self._fail[f]
                fallback = self._next[f].get(ch, 0)
                self._fail[child] = 0 if fallback == child else fallback
                self._out[child].extend(self._out[self._fail[child]])
                q.append(child)

    def find(self, text: str) -> Iterator[tuple[int, int, int]]:
        """yield (pattern_index, start, end)。"""
        node = 0
        norm = self._norm(text)
        for i, ch in enumerate(norm):
            while node and ch not in self._next[node]:
                node = self._fail[node]
            node = self._next[node].get(ch, 0)
            for pat in self._out[node]:
                length = len(self._patterns[pat])
                yield pat, i - length + 1, i + 1

    def pattern(self, idx: int) -> str:
        return self._patterns[idx]

    def __len__(self) -> int:
        return len(self._patterns)


def build(patterns: Iterable[str], case_insensitive: bool = True) -> AhoCorasick:
    ac = AhoCorasick(case_insensitive=case_insensitive)
    for p in patterns:
        ac.add(p)
    ac.build()
    return ac
