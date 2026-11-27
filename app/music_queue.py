"""Ordered, mutable, in-memory queue for Discord music playback.

Indexes shown to users start at 1 and refer only to UPCOMING songs.
The currently playing track is managed separately.
"""

import asyncio
import random
from collections import deque
from typing import Generic, TypeVar

T = TypeVar("T")


class MusicQueue(Generic[T]):
    def __init__(self, max_size: int = 150):
        self.max_size = max_size
        self._items: deque[T] = deque()
        self._changed = asyncio.Event()

    def __len__(self):
        return len(self._items)

    def snapshot(self) -> list[T]:
        return list(self._items)

    def add_many(self, tracks: list[T], *, front: bool = False):
        if len(self._items) + len(tracks) > self.max_size:
            raise ValueError(f"คิวเพลงรองรับได้สูงสุด {self.max_size} เพลงค่ะ")
        if front:
            # Preserve playlist order when multiple tracks are inserted at front.
            self._items.extendleft(reversed(tracks))
        else:
            self._items.extend(tracks)
        if tracks:
            self._changed.set()

    async def get(self) -> T:
        while not self._items:
            self._changed.clear()
            await self._changed.wait()
        item = self._items.popleft()
        if not self._items:
            self._changed.clear()
        return item

    def remove(self, position: int) -> T:
        if position < 1 or position > len(self._items):
            raise ValueError("ไม่พบเพลงหมายเลขนี้ในคิวค่ะ")
        items = list(self._items)
        removed = items.pop(position - 1)
        self._items = deque(items)
        if not self._items:
            self._changed.clear()
        return removed

    def move(self, source: int, destination: int):
        if min(source, destination) < 1 or max(source, destination) > len(self._items):
            raise ValueError("กรุณาตรวจสอบหมายเลขเพลงต้นทางและปลายทาง")
        items = list(self._items)
        items.insert(destination - 1, items.pop(source - 1))
        self._items = deque(items)

    def shuffle(self):
        items = list(self._items)
        random.shuffle(items)
        self._items = deque(items)

    def clear(self):
        self._items.clear()
        self._changed.clear()
