"""Pepe's mouth: one interface, swappable (one interface per provider).

The real one (phase 1, needs the speakerphone and the ElevenLabs key) turns text into speech and plays it, and
stops when someone talks over it. The fake one records what would have been said, for tests and the simulator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class Voice(Protocol):
    def say(self, text: str, settings: dict) -> bool:
        """Say it out loud (blocking). settings: speech_rate and volume (feedback.voice_settings).
        Returns False if it couldn't be said, or was cut off before the end (someone talked over it)."""
        ...

    def chime(self) -> None:
        """A soft sound: "something for you, when you can" (a private reminder with visitors there)."""
        ...


@dataclass
class FakeVoice:
    said: list[str] = field(default_factory=list)
    settings: list[dict] = field(default_factory=list)
    chimes: int = 0
    broken: bool = False      # the speaker is unplugged: nothing gets said
    cut_off: set[str] = field(default_factory=set)  # lines someone talks over

    def say(self, text: str, settings: dict) -> bool:
        if self.broken:
            return False
        self.said.append(text)
        self.settings.append(settings)
        return text not in self.cut_off

    def chime(self) -> None:
        self.chimes += 1


class PrintVoice:
    """On the Mac without a speaker: print instead of speaking."""

    def say(self, text: str, settings: dict) -> bool:
        print(f"[Pepe] {text}", flush=True)
        return True

    def chime(self) -> None:
        print("[Pepe] (tilín)", flush=True)
