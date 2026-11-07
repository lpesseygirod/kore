"""The setup conversation (PEPE.md 2b), as steps code keeps track of.

Claude words each question naturally, one at a time; code decides which step
comes next, for whom, and what counts as done. Every answer that changes
something goes through the usual tools: names, medicines, appointments,
contacts and permissions are confirmed with a clear yes (checked by code).

Adapted to v1 (BUILD-PLAN 1): one voice and the fixed name "Pepe" (choosing
a voice or renaming Pepe come later, a new name needs a new wake word); no
voice recognition (Pepe asks who is speaking); no 112. Added: tú or usted.

scope: "home" steps are done once for the home; "person" steps once for each
person, answered by that person (code checks who is speaking).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    id: str
    scope: str  # "home" | "person"
    ask: str    # what to ask, for Claude to word (in Spanish, one question)
    save: str   # how the answer is saved
    two_people_only: bool = False


STEPS = [
    Step("welcome", "home",
         "Welcome them warmly in two or three sentences: you're here to help with reminders, questions or a chat; "
         "they can ask you anything and change anything just by telling you. Then ask whether they'd like to get to "
         "know each other now or another day.",
         "If they want to start: setup_step_done. If not now: setup_pause (you'll offer again another day)."),
    Step("people", "home",
         "Ask their name and how they like to be called; then whether anyone else lives with them.",
         "propose_person for each person (it reads back the name), then confirm_action on their yes. When everyone "
         "is in: setup_step_done."),
    Step("address", "person",
         "Ask whether they'd like you to call them tú or usted.",
         "setup_setting address = tu or usted (that closes the step)."),
    Step("hearing", "person",
         "Ask whether they hear you well, or would like you slower or louder.",
         "setup_setting speech_rate (slower/faster) or volume (louder/softer) if they ask; else setup_step_done."),
    Step("wants", "person",
         "Ask what they'd like from you: reminders, answering questions, company, or a bit of everything.",
         "remember_about_person kind preference, then setup_step_done."),
    Step("chattiness", "person",
         "Ask whether they'd like you to start a conversation sometimes, or only speak when they call you.",
         "setup_setting chattiness first if they want more or less, then propose_permission conversation_starters "
         "(or record_permission_no), which closes the step."),
    Step("nudges", "person",
         "Ask whether they'd like an occasional reminder to drink water or to move a little after sitting a long "
         "time. One question for both.",
         "propose_permission water_nudges and/or movement_nudges, or record_permission_no for each; then "
         "setup_step_done."),
    Step("quiet_times", "home",
         "Ask when they'd rather you stayed quiet: what time they go to bed and get up, and whether they nap.",
         "setup_setting night_start, night_end, rest_hours (HH:MM-HH:MM); then setup_step_done."),
    Step("health_info", "person",
         "Ask, in one sentence, whether you may keep their medicines and appointments in order to remind them.",
         "propose_permission health_info, or record_permission_no (either closes the step)."),
    Step("medicines", "person",
         "Ask whether they'd like reminders for any medicines: which ones, and at what times. One at a time.",
         "propose_medication for each, then confirm_action on their yes. When there are no more: setup_step_done."),
    Step("dates", "person",
         "Ask whether there are appointments coming up, or family birthdays they don't want to miss.",
         "propose_appointment / propose_contact (with the birthday), each confirmed. Then setup_step_done."),
    Step("help_contact", "person",
         "Ask who you should contact if they ever need help (a name and how they're related).",
         "propose_contact with help = true, confirmed (that closes the step). If there's nobody: setup_step_done "
         "with skipped. Their family sets up the phone or message link later."),
    Step("listening", "person",
         "Explain in one sentence that you only listen after they say your name or press the button, and ask if "
         "that's all right.",
         "propose_permission listening, or record_permission_no (either closes the step)."),
    Step("presence", "person",
         "Ask whether you may notice when they're up or moving around, to greet them and check they're all right.",
         "propose_permission presence_sensor, or record_permission_no (either closes the step)."),
    Step("family_alerts", "person",
         "Ask whether, if they miss their medicine several times in a row, you may let their help contact know.",
         "propose_permission family_missed_meds, or record_permission_no (either closes the step)."),
    Step("emergency", "person",
         "Ask whether, if they say 'Pepe, ayuda', tell you they've fallen, or press the button, you may message "
         "their help contact straight away, without asking first. (If not, you'll always ask first.)",
         "propose_permission emergency_path, or record_permission_no (either closes the step)."),
    Step("share", "person",
         "Ask whether the other person at home may hear from you whether they've taken their medicine.",
         "propose_permission share_with_partner, or record_permission_no (either closes the step).", two_people_only=True),
    Step("test", "home",
         "Suggest a short test: they say 'Pepe, ¿qué hora es?' and you answer.",
         "setup_step_done after the test (or if they'd rather not)."),
    Step("finish", "home",
         "Thank them: that's all for now, and they can change anything whenever they like, just by telling you.",
         "setup_step_done."),
]

BY_ID = {s.id: s for s in STEPS}


# Steps whose whole answer is one permission. If that permission is already on record (given or refused,
# in an earlier chat or by the family's setup), the step is answered: Pepe doesn't ask it again.
PERMISSION_STEPS = {"health_info": "health_info", "listening": "listening", "presence_sensor": "presence",
                    "family_missed_meds": "family_alerts", "emergency_path": "emergency",
                    "share_with_partner": "share"}
