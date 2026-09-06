"""BFS frontier: dedupe on a link's identity key, carry its request spelling along."""

from __future__ import annotations

from collections import deque

from seo_scout.urls import Link


class Frontier:
    def __init__(self, *, max_depth: int) -> None:
        self._max_depth = max_depth
        self._queue: deque[tuple[Link, int]] = deque()
        self.seen: set[str] = set()

    def add(self, link: Link, depth: int) -> bool:
        """Queue a page once by `link.key`; `link.url` is the spelling that will be fetched.

        Taking a `Link` rather than a bare key means no caller can fall back to fetching
        the normalised form, which manufactures redirects.
        """
        if depth > self._max_depth or link.key in self.seen:
            return False
        self.seen.add(link.key)
        self._queue.append((link, depth))
        return True

    def mark_seen(self, key: str) -> None:
        self.seen.add(key)

    def pop(self) -> tuple[Link, int] | None:
        return self._queue.popleft() if self._queue else None

    def __len__(self) -> int:
        return len(self._queue)
