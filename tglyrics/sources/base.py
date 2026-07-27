from __future__ import annotations

import abc

from ..clock import PlaybackClock


class Source(abc.ABC):

    name = "source"

    def __init__(self, clock: PlaybackClock) -> None:
        self.clock = clock

    @abc.abstractmethod
    async def start(self) -> None: ...

    @abc.abstractmethod
    async def stop(self) -> None: ...

    def describe(self) -> str:
        return self.name
