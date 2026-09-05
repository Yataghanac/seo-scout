"""BFS frontier: dedupe on the normalized key, remember the spelling to request."""

from __future__ import annotations

from collections import deque


class Frontier:
    def __init__(self, *, max_depth: int) -> None:
        self._max_depth = max_depth
        self._queue: deque[tuple[str, int, str]] = deque()
        self.seen: set[str] = set()

    def add(self, url: str, depth: int, request: str | None = None) -> bool:
        """Queue a page once by its normalized `url`; `request` is the spelling to fetch."""
        if depth > self._max_depth or url in self.seen:
            return False
        self.seen.add(url)
        self._queue.append((url, depth, request or url))
        return True

    def mark_seen(self, url: str) -> None:
        self.seen.add(url)

    def pop(self) -> tuple[str, int, str] | None:
        return self._queue.popleft() if self._queue else None

    def __len__(self) -> int:
        return len(self._queue)
