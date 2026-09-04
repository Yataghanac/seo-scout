"""BFS frontier with dedupe on normalized URL and a depth ceiling."""

from __future__ import annotations

from collections import deque


class Frontier:
    def __init__(self, *, max_depth: int) -> None:
        self._max_depth = max_depth
        self._queue: deque[tuple[str, int]] = deque()
        self.seen: set[str] = set()

    def add(self, url: str, depth: int) -> bool:
        """Queue a URL once. Returns False if already seen or beyond max depth."""
        if depth > self._max_depth or url in self.seen:
            return False
        self.seen.add(url)
        self._queue.append((url, depth))
        return True

    def mark_seen(self, url: str) -> None:
        self.seen.add(url)

    def pop(self) -> tuple[str, int] | None:
        return self._queue.popleft() if self._queue else None

    def __len__(self) -> int:
        return len(self._queue)
