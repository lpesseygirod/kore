"""Conversation state that code, not Claude, controls.

- Turns: each thing the person says is a turn. A confirmation must come in a
  later turn than the question, and must be a clear yes (language.py).
- Who is speaking: in a two-person home, nobody is "the speaker" until they
  say who they are, or answer yes to "Is that you, Carmen?". Personal answers
  ("I took it") only count for a known speaker.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from kore.brain.language import is_affirmative, self_identified

# A pending confirmation must be answered in the very next turn.
CONFIRM_WITHIN_TURNS = 1


class SpeakerUnknown(Exception):
    def __init__(self, candidate_id: int | None):
        super().__init__("speaker_unknown")
        self.candidate_id = candidate_id


class SpeakerMismatch(Exception):
    def __init__(self, speaker_id: int, person_id: int):
        super().__init__("speaker_mismatch")
        self.speaker_id = speaker_id
        self.person_id = person_id


class ConfirmationRefused(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class Person:
    id: int
    name: str
    call_name: str

    @property
    def aliases(self) -> list[str]:
        return list(dict.fromkeys([self.call_name, self.name]))


@dataclass
class PendingAction:
    id: int
    kind: str
    payload: dict
    readback: str
    created_turn: int
    speaker_id: int | None
    status: str = "pending"  # pending | done | cancelled | expired


@dataclass
class Session:
    household: list[Person]
    turn: int = 0
    user_text: str = ""
    speaker_id: int | None = None
    identity_question: tuple[int | None, int] | None = None  # (candidate, asked at turn)
    pending: dict[int, PendingAction] = field(default_factory=dict)
    # The web chat: the login says who is writing, and nothing they type ("soy Carmen") changes it.
    fixed_speaker: int | None = None
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))

    def __post_init__(self):
        self._auto_speaker()

    # --- turns -------------------------------------------------------------

    def begin_turn(self, text: str) -> None:
        """Called with each new thing the person says, before Claude sees it."""
        self.turn += 1
        self.user_text = text
        self._resolve_identity(text)
        for p in self.pending.values():
            if p.status == "pending" and self.turn > p.created_turn + CONFIRM_WITHIN_TURNS:
                p.status = "expired"

    def end_conversation(self) -> None:
        """Silence after the conversation: forget who was speaking and any open question."""
        self.speaker_id = None
        self.identity_question = None
        for p in self.pending.values():
            if p.status == "pending":
                p.status = "expired"
        self._auto_speaker()

    # --- who is speaking ---------------------------------------------------

    def person(self, person_id: int) -> Person:
        for p in self.household:
            if p.id == person_id:
                return p
        raise KeyError(person_id)

    def add_person(self, person: Person, speaking: bool) -> None:
        """Someone new at home (setup). If they introduced themselves, they're the speaker now."""
        self.household.append(person)
        if speaking and self.fixed_speaker is None:
            self.speaker_id = person.id
            self.identity_question = None
        # No _auto_speaker here: someone they told us about never becomes "the speaker".

    def rename(self, person_id: int, call_name: str) -> None:
        self.person(person_id).call_name = call_name

    def find_person(self, name: str) -> Person | None:
        from kore.brain.language import fold

        for p in self.household:
            if fold(name) in (fold(a) for a in p.aliases):
                return p
        return None

    def ask_identity(self, candidate_id: int | None = None) -> str:
        """Record that Pepe is asking who is speaking; returns the question to say."""
        self.identity_question = (candidate_id, self.turn)
        if candidate_id is not None:
            return f"Is that you, {self.person(candidate_id).call_name}?"
        return "Sorry, who am I talking to?"

    def require_speaker(self, person_id: int) -> None:
        """Raise unless the known speaker is `person_id`. Asks if unknown."""
        if self.speaker_id is None:
            self.ask_identity(person_id)
            raise SpeakerUnknown(person_id)
        if self.speaker_id != person_id:
            raise SpeakerMismatch(self.speaker_id, person_id)

    def _auto_speaker(self) -> None:
        if self.fixed_speaker is not None:
            self.speaker_id = self.fixed_speaker
        elif len(self.household) == 1:
            self.speaker_id = self.household[0].id

    def _resolve_identity(self, text: str) -> None:
        if len(self.household) == 1 or self.fixed_speaker is not None:
            return
        names = {p.id: p.aliases for p in self.household}
        q = self.identity_question
        answering = q is not None and self.turn == q[1] + 1
        pid = self_identified(text, names, answering=answering)
        if pid is not None:
            self.speaker_id = pid
            self.identity_question = None
            return
        if q is not None:
            candidate, asked_turn = q
            if self.turn == asked_turn + 1 and candidate is not None and is_affirmative(text):
                self.speaker_id = candidate
            self.identity_question = None

    # --- confirmations -----------------------------------------------------

    def propose(self, kind: str, payload: dict, readback: str) -> PendingAction:
        # Only the latest read-back can be confirmed: a "sí" answers what Pepe just asked.
        for p in self.pending.values():
            if p.status == "pending":
                p.status = "expired"
        action = PendingAction(next(self._ids), kind, payload, readback, self.turn, self.speaker_id)
        self.pending[action.id] = action
        return action

    def take_confirmed(self, action_id: int) -> PendingAction:
        """Return the action if the person's latest words confirm it; else raise."""
        action = self.pending.get(action_id)
        if action is None:
            raise ConfirmationRefused("no_such_pending_action")
        if action.status != "pending":
            raise ConfirmationRefused(f"action_{action.status}")
        if self.turn <= action.created_turn:
            raise ConfirmationRefused("must_wait_for_their_answer")
        if action.speaker_id is not None and self.speaker_id != action.speaker_id:
            raise ConfirmationRefused("different_speaker")
        if not is_affirmative(self.user_text):
            raise ConfirmationRefused("not_a_clear_yes")
        action.status = "done"
        return action

    def cancel(self, action_id: int) -> None:
        action = self.pending.get(action_id)
        if action and action.status == "pending":
            action.status = "cancelled"
