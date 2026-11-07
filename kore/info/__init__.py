"""Information from outside the home: the weather and the news."""

from __future__ import annotations

from dataclasses import dataclass

from kore.info.news import News
from kore.info.weather import Weather


@dataclass
class Info:
    weather: Weather | None = None
    news: News | None = None

    def tick(self) -> None:
        """The device loop: keep both fresh in the background, so a turn rarely waits for the network."""
        for source in (self.weather, self.news):
            if source is not None:
                try:
                    source.refresh(wait=0)  # never blocks the loop: a finished fetch is stored next tick
                except Exception:
                    pass
