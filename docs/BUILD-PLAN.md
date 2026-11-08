# KORE — Build Plan

How to turn the KORE design document into a working device (KORE is the product; Pepe is the companion's default name and wake word), built mostly by Claude Code on your Mac. It covers the architecture, the services to use, the risks, the phases with clear "done" checks, what only you can do, and a ready-to-paste starting prompt.

## 0. My verdict on your idea

**I agree with building Pepe on APIs** (cloud speech-to-text, Claude as the brain, cloud voices). It's the fastest way to a version your grandparents can use, and it reuses what you already know from Studiply (ElevenLabs, Supabase).

**I agree only partly with "Claude does it 100% on its own".** Claude Code can write and test almost all of the software by itself. But it can't buy hardware, plug things in, create accounts, type your API keys, or sit with your grandparents. Those parts are yours (section 9). The plan is arranged so that you're only needed at a few clear moments.

Three changes I'd make to the design before building:

1. **Build and test everything on your Mac first, with a fake clock.** Claude Code can then simulate a whole day (or a week) in minutes and test itself without you. The Raspberry Pi comes only at the end.
2. **Anything safety-related must not depend on the AI.** Medication times, "did I take it", alerts to family and the emergency phrase all run as plain, tested code. Claude chooses the words, never the facts or the timing.
3. **Don't promise fall detection or automatic calls to 112 in version 1.** Detecting a fall from a sound is unreliable, and a family that trusts it could be worse off than without it. Version 1 keeps "Pepe, help" plus a big physical button, and messages family. Sound-based fall detection becomes a later experiment.

## 0b. Critical review: what was wrong with the first version

I went back through this plan as if someone else had written it. These are the real problems, most serious first. Each fix is now built into the sections below (marked **new**).

### Contradictions in the design (would break the product)

1. **"I only listen when you say my name" isn't true for half the design.** Offering help when it hears them struggling, noticing visitors, hearing a thud, noticing the TV is loud: all of these need the microphone analysing sound all the time. **Fix:** v1 really listens only after "Pepe" or the button. Anything that analyses sound continuously comes later, runs only on the device, is never stored or sent, and the consent sentence at setup must say exactly that. Sections 2b and 4 of the design doc need updating to match.
2. **Your trial is with two people, but v1 had no way to tell them apart.** With your grandparents, "Did you take your pill?" can be answered by the wrong person. That's a medication error. **Fix:** v1 handles two people without voice biometrics. Reminders always use the person's name, a personal answer only counts once Pepe knows who's speaking ("Is that Carmen?"), and each person has separate medication records.
3. **The speed target was impossible.** The plan asked for under 2 seconds from end of speech, and also for waiting 1.5–2 seconds of silence so older people aren't cut off. Both can't be true. **Fix:** a realistic budget (section 2). Pepe shows instantly that it heard them (a soft sound and the light), and speaks within about 3 seconds. "Finished speaking" is judged by meaning as well as silence, so the wait is shorter when the sentence is clearly complete.
4. **Renaming Pepe isn't a quick voice command.** Each new name needs a new wake word model, which has to be trained. **Fix:** v1 has a fixed name. When renaming arrives, it's a slower step ("give me until tomorrow to learn my new name"), with the button working meanwhile.
5. **"Pepe" may be a poor wake word.** It's short, two syllables, and a common name in Spain, so it will come up on TV and from visitors. **Fix:** test it in the first week against hours of TV audio. If false wake-ups are too frequent, switch to "Oye Pepe" or a longer name.

### Safety (the parts that could hurt someone)

6. **KORE must not become their emergency system.** Spain already has public teleasistencia: a pendant button (with fall detection) linked to a 24-hour centre. In Madrid it costs at most €12 a month depending on income, and it's free for people over 87. **Fix:** your grandparents should have teleasistencia, or keep it, and KORE adds to it. KORE's "help" is a convenience, never a guarantee. This is also a business opening (phase 9).
7. **"Pepe, help" quietly failed without internet.** It depends on the internet and a messaging service. **Fix:** if the message can't be sent, Pepe says so out loud straight away and tells them to use their phone or teleasistencia button, instead of seeming to have called someone.
8. **After a power cut, the clock could be wrong, and the medication reminders with it.** The Raspberry Pi 5 has a clock chip, but it keeps time while unplugged only with its small backup battery fitted. Without it, time freezes until the internet fixes it. **Fix:** buy the RTC battery. If the clock can't be trusted, Pepe doesn't fire or log doses, and says why.
9. **SD cards corrupt on power cuts, and the dose log lives there.** **Fix:** an SSD or a high-endurance card, a database set up to survive sudden power loss, and nightly encrypted backups off the device.
10. **Outside text can trick the AI.** News headlines and family messages pass through Claude, so a message saying "ignore your rules and…" could influence it. **Fix:** outside text is always treated as quoted content, can never trigger tools, and this is tested.

### Cost and complexity (would make it slow or expensive)

11. **I underestimated the Claude cost.** The design rules make a long system prompt, sent with every turn, plus memory and the conversation. ElliQ's users talk to it about 20 times a day, and Pepe also speaks first. Without caching, Claude becomes the biggest cost. **Fix:** prompt caching from day one (section 5).
12. **WhatsApp Business is heavy for a trial with two people.** It needs Meta business verification, a dedicated number and pre-approved templates. **Fix:** the trial uses a Telegram bot (free, instant, and family can reply, so Pepe can read replies aloud), plus an SMS for "help". WhatsApp comes before selling.
13. **Supabase isn't needed in v1.** **Fix:** a free heartbeat monitor and an encrypted nightly backup. Supabase comes back when there are many devices.

### Building it hands-off (your main goal)

14. **There were no early risk checks.** The three things most likely to sink the project (speed, understanding older Spanish voices, false wake-ups) only showed up in phase 1 or on the device. **Fix:** a new phase 0.5 of quick experiments before building the product.
15. **AI tests give different answers each run.** One passing run proves little. **Fix:** each conversation scenario runs several times and must pass nearly every time. Normal test runs replay recorded answers, so they're free and repeatable, and live API runs have a daily budget.
16. **"Claude does it alone" needed more than a starting prompt.** Long unattended work needs a state file to resume from, spending caps, stop conditions and someone checking the work. **Fix:** section 12, an advisor setup like the one you use for Studiply, where one Claude session drives and checks Claude Code.
17. **Your grandparents' privacy during the trial.** To fix problems, you'd read transcripts of their private conversations. **Fix:** agree it with them first, keep transcripts only 7 days, let them say "don't save that", encrypt the device's disk, and turn off data use for training at each provider.
18. **No way to measure "before".** The trial compares missed medication before and after, but nothing measured before. **Fix:** one baseline week, with a family member checking the pill box daily, before KORE arrives.

## 1. What version 1 includes (and what waits)

> **Added 2026-11-08 (Lea agreed):** a private web page for each box, reached on the home Wi-Fi (chat, photos,
> calendar, the same memory as the voice). The plan is in WEB-PLAN.md. A family app beyond that still waits.

Version 1 is the scope from your plan (section 8 of the Pepe doc), for your grandparents only:

- Talking by voice in one language (the variety they speak at home), with one voice and a fixed wake word, plus the button
- **Listening only after the wake word or the button** (new): nothing analyses sound in between
- **Two people in the home** (new): each with their own name, medication and reminders. Pepe addresses them by name and asks who's speaking before accepting a personal answer. No voice biometrics.
- Medication reminders with "Did you take it?", a dose log and never-double rules
- Water and movement nudges, appointments, birthdays, recurring reminders made by voice
- The shopping list (including "I'm going shopping" / "I'm back")
- Questions: time, date, weather, simple explanations, news headlines on request
- Morning greeting (presence sensor; greets whoever is there without assuming who), quiet mode, night silence
- Memory of the person: interests, people, life stories, preferences, "What do you know about me?", "Forget that"
- "I don't feel well" → offers to message family (asks first)
- "Pepe, help" and the button → messages family straight away (only if agreed at setup). **If it can't, it says so out loud** (new) and points them to their phone or teleasistencia button.
- Setup as a conversation (the script in section 2b of the design doc), for both people. Plus feedback that Pepe applies straight away, with at most one follow-up question, logged on a page you can check and undo (new, section 2)

**Waits for later** (each one is hard or risky, and none is needed to learn whether Pepe works):

- Choosing among several voices, and renaming Pepe (a new name needs a new wake word model)
- Recognising people by voice (biometric data under the GDPR)
- **Anything that listens without the wake word** (new): hearing them struggle, noticing visitors, TV or phone calls, hearing a thud. v1: they say "I have visitors" and Pepe goes discreet.
- Sound-based fall detection, calling 112
- The family app and WhatsApp (v1: Telegram messages and a heartbeat monitor)
- Music, audiobooks, brain games, calls by voice

## 2. How it works (architecture)

### On the device (in their home)

- **Audio:** microphone and speaker with echo cancellation, so Pepe doesn't hear itself.
- **Wake word:** recognised on the device. Nothing is sent anywhere until they say "Pepe" or press the button.
- **Voice activity detection:** decides when they've finished speaking. Older people pause a lot, so the silence needed before Pepe answers must be long (about 1.5–2 seconds) and adjustable.
- **Scheduler:** a local database with all reminders and the dose log. It fires reminders like alarms, even with no internet.
- **Presence sensor:** up / sitting still / no one there.
- **Big button and light ring:** listening, speaking, quiet mode, reminder waiting.
- **Offline fallback:** pre-recorded phrases for medication reminders and "I'm having trouble connecting, your reminders still work."

### In the cloud (through APIs)

- **Speech to text:** ElevenLabs Scribe v2 Realtime, which streams with about 150 ms latency, unless the phase 0.5 experiment picks another provider. The stream opens only after the wake word and closes when they finish, since it's billed by streamed time.
- **The brain:** Claude through the API, with a system prompt built from the design doc and **tools** for everything that touches facts (reminders, lists, memory, messages). **Today's state** (new) (doses due and taken, the list, appointments, who's home) goes into the prompt, so most replies need no tool call. **Prompt caching** is on from day one.
- **Text to speech:** ElevenLabs Flash (very low latency) in a warm, slow voice, streamed sentence by sentence.
- **Messages to family** (new): a Telegram bot for v1 (free, two-way: family can reply and Pepe reads it aloud on request), plus an SMS (for example through Twilio) for "help". WhatsApp Business comes before selling.
- **Weather and news** (new): Open-Meteo for weather (free, no key); headlines from a public broadcaster's RSS feed (check its terms), summarised, never invented.
- **Monitoring and backup** (new): a heartbeat to a free "dead man's switch" service (for example Healthchecks.io) that alerts you if the device goes quiet; a nightly encrypted backup of the database. Supabase (EU) comes later for many devices, and API keys move to a backend before selling.

### The one rule that matters most

Claude decides **what to say**. Code decides **what is true and when**. Concretely:

- Claude never stores or reads medication times from its own memory. It calls tools like `get_med_status`, `mark_dose_taken`, `add_reminder`.
- Tools enforce the rules: a dose can't be logged twice; a medication can't be added or changed without a spoken confirmation; nothing goes to family without a clear yes (except the emergency path agreed at setup).
- The "when to speak" engine (categories 1–3, daily limits, quiet mode, night, visitors) is plain code. It asks Claude only to word the message.

### Speed budget (new)

| Step | Target |
| --- | --- |
| They stop speaking → Pepe decides they've finished | 0.7–1.5 s (shorter when the sentence is clearly complete) |
| Sound and light showing "I heard you" | immediately |
| Final transcript | about 0.2 s |
| Claude's first sentence | about 1 s (no tool call for simple reads; a faster model for simple turns) |
| First audio | about 0.2 s |
| **End of speech → first word** | **about 2–3 s** |

Every turn logs its timings. A turn over 4 seconds counts as a failure in tests. If they speak while Pepe is talking, Pepe stops and listens.

### The feedback loop (new)

When either of them says something about how Pepe behaves, Pepe learns from it straight away and keeps a record you can check.

**What counts as feedback:** direct requests ("talk slower", "don't wake me so early", "that was too long"), complaints ("you never let me finish"), praise ("I like it when you ask about my book") and corrections. Corrections of facts ("no, the pill is at 10") go through the normal tools; feedback about how Pepe behaves goes to the feedback system.

**What Pepe does, in order:**

1. **Acknowledges in one sentence and applies it at once:** "Of course, I'll speak more slowly." The change is saved and used from the next sentence on, in every later conversation.
2. **Asks why, only if it isn't clear.** One short, natural question at most: "Was it too fast, or too loud?", "Is it mornings in general, or just today?" If they don't want to explain, Pepe drops it. Never a second question.
3. **Settles the scope:** just now, today or from now on; this person or both. If it's unclear, the follow-up asks it ("Just today, or always?"). By default: from now on, for that person only.
4. **Checks once, a few days later, lightly:** "I've been speaking more slowly. Is that better?" (within the design's limit of one such check a day).

**What it never does on its own:**

- **Weaken a safety rule.** "Stop reminding me about my pill" is logged and answered kindly ("I'll keep it short and quiet. Would you like to mention it to your doctor next time?"), but the reminder stays unless the person confirms clearly on two different days, and the family member is told if they agreed to that at setup. The same goes for help alerts.
- Change something for the other person.
- Turn a one-off into a rule when they said "just today".

**How it learns (no retraining):**

- Each item is stored as a record: date, who, what they said, how Pepe understood it, the answer to the follow-up, the change made, its scope, and its status (active, undone, replaced).
- Things with a clear setting (speed, volume, quiet hours, reminder times, how chatty Pepe is) are changed directly by code.
- Everything else becomes a short written rule for that person ("Carmen: don't bring up her sister's health"), added to Pepe's prompt from the next turn.
- Rules are merged and capped so the prompt doesn't grow forever. If two rules conflict, the newer one wins and the older one is marked replaced.
- Indirect signals ("be quiet" three afternoons in a row) are noticed but never applied silently. Pepe suggests instead: "You often rest around 4. Shall I stay quiet then?"

**Your feedback page (for you):**

- A simple private page (in the trial, also a weekly Telegram summary) listing every item: what was said, how Pepe understood it, what changed, and whether it's still active.
- You can undo any change, reword a rule, or mark "Pepe misunderstood". Undoing works from the next turn, without Pepe announcing it.
- Anything that touched medication, alerts or night rules is listed first.
- It shows the feedback, not the private conversation around it: only the feedback sentence is stored. Your grandparents are told at setup that you'll see their feedback about Pepe.
- Each item becomes a test scenario, so a fixed problem can't come back unnoticed.

## 3. Hardware for the first device

Buy the speakerphone in phase 0, since the experiments need it; the rest once the Mac version works (phase 7). Rough cost: under €300.

- **Raspberry Pi 5 (8 GB)** with the **active cooler** (it runs all day), official power supply, a case
- **The official RTC battery** (new): keeps the clock running through power cuts, so medication times stay right
- **An SSD (M.2 HAT) or a high-endurance microSD** (new): normal cards corrupt on power cuts
- **A USB conference speakerphone with built-in echo cancellation** (the Jabra Speak or Anker PowerConf type). This is the most important part: a cheap mic and speaker make Pepe hear itself and miss quiet voices. Check it's loud enough for them.
- **LD2410C mmWave presence sensor.** Unlike normal motion sensors, it detects people sitting still, and it works with the Raspberry Pi. Tune its range: it senses through walls, and pets can trigger it.
- **A large arcade-style button** and a small LED ring
- **Recommended:** a small battery backup (UPS) so a power cut doesn't stop reminders; a 4G dongle if their Wi-Fi is unreliable

Before buying, check: is there stable Wi-Fi where KORE will sit, and a socket near where they spend most of the day? One device covers one room, so agree with them which room (usually the living room or kitchen), and accept that reminders won't be heard from the bedroom in v1.

## 4. Risks I'd take seriously

- **Echo and false wake-ups:** TV dialogue saying something like "Pepe" will trigger it. Tune the threshold on real recordings of their living room, and mute wake word detection while Pepe is speaking.
- **Latency:** more than about 3 seconds of silence after they speak feels broken. Stream all three steps (speech to text → Claude → voice) and measure every reply.
- **Accuracy in Spanish with older voices:** test with real recordings of your grandparents early (phase 1). If ElevenLabs struggles, try another provider; Soniox, for example, claims better accuracy on Spanish (that's their own claim, so test it).
- **The AI saying something wrong about health:** medication answers come only from tools; the system prompt forbids dose advice; scenario tests check it (phase 4).
- **Silent failure:** if Pepe crashes or loses Wi-Fi, nobody notices. The device sends a heartbeat every few minutes; if it stops for more than an hour during the day, **you** get a message. Family must be told clearly that Pepe is not an alarm system.
- **Keys on the device:** fine for your grandparents; for selling, devices must call your backend, never hold the API keys themselves.
- **The wake word model's licence:** openWakeWord's code is open source; train your own "Pepe" model and check the licence of any ready-made model before selling.
- **Medical-device rules (EU MDR):** a reminder tool is usually low-risk, but anything that monitors health or advises on treatment can make the product a medical device. Keep the purpose "daily organisation and company", never "health monitoring", and get a lawyer's view before selling. For a trial with your own grandparents this isn't a blocker.
- **GDPR:** health data and voice. Clear spoken consent at setup (already in the design), data in the EU, a data processing agreement with each provider before selling.

## 5. Monthly running cost (estimate for one home)

Assuming two people, about 30 minutes of talking a day in total, and about 60 Claude calls a day (their turns plus Pepe speaking first):

- Speech to text (realtime, about $0.39 per hour of streamed audio, only after the wake word): around $6
- Voice (Flash, about $0.05 per 1,000 characters, about 5,000 a day): around $8
- **Claude API** (corrected): the long system prompt (design rules, memory, today's state) is sent with every call. **With prompt caching**, cached input costs a small fraction of the normal price (5% for Sonnet 5.5), so this stays at a few euros a month. **Without caching**, it could become the largest cost. Conversations are often more than 5 minutes apart, so use the 1-hour cache: writing it costs twice the normal input price, reading it is cheap.
- Telegram: free. SMS for "help": a few cents each.
- Heartbeat monitor and backups: free tier.

**Total: roughly €15–25 a month per home, if caching works.** Measure it from day one: the logs record the cost of every turn.

For comparison, ElliQ (the best-known companion device, sold in the US) charges a $249 enrolment fee plus a monthly subscription of roughly $30–60 depending on the plan. A KORE subscription around €30–50 a month would leave room for the device, support and a margin. That's a guess to test, not a price.

## 6. Folder and code layout

One repo, `~/Documents/Projects/Apps/KORE`, Python on both Mac and Pi:

```
KORE/
  CLAUDE.md            rules for Claude Code
  docs/PEPE.md         the design doc (exported from Claude)
  docs/BUILD-PLAN.md   this plan
  docs/PROGRESS.md     what's done, kept up to date
  docs/reports/        Claude Code's reports (git-ignored)
  kore/
    audio/             mic, speaker, wake word, voice activity
    stt/  tts/         providers behind one interface each
    brain/             Claude client, system prompt, tools
    memory/            SQLite schema and access
    scheduler/         reminders, dose log, fake clock
    manners/           when to speak: categories, limits, quiet, night
    feedback/          spotting feedback, follow-up, rules, log, your page
    family/            Telegram and SMS messages, heartbeat
    hardware/          sensor, button, LED (Pi only)
  experiments/         phase 0.5 throwaway experiments
  sim/                 text and voice simulator, day scenarios
  tests/               unit tests and scenario tests
```

## 7. The phases

Each phase ends with checks Claude Code runs itself, one local commit, and a short report. You're needed only where it says **(Lea)**.

### Phase 0 — Setup (Lea, about 1 hour)

- Create the folder, export the KORE design doc and this plan as Markdown into `docs/`
- Create accounts and keys: Anthropic API, ElevenLabs; a Telegram bot (through BotFather) for family messages
- Put the keys in a `.env` file yourself (hidden typing), and set a monthly spending limit with each provider
- Buy the USB speakerphone now (the experiments need it)
- Record 5–10 short clips of each grandparent speaking normally (with their OK), plus 2–3 hours of audio from the TV they usually watch
- Talk to your grandparents: their consent to the trial and to you reading transcripts; do they have teleasistencia?
- Decide section 10

### Phase 0.5 — Risk experiments (new, about a week)

Quick throwaway experiments before building the product. Each one can change the plan.

1. **Speed:** a minimal loop (button → speech to text → Claude → voice), measuring each step. Goal: about 3 seconds or less from end of speech to first word.
2. **Understanding older voices:** run your grandparents' clips through ElevenLabs Scribe and one alternative (for example Soniox), and compare the errors on 20 phrases. Pick the better one.
3. **False wake-ups:** train a "Pepe" wake word model and play the TV audio at it. Count false wake-ups per hour and missed calls. Decide between "Pepe" and "Oye Pepe".
4. **Personality:** a 30-minute text conversation with a first system prompt built from the design doc. You read it: does it sound like Pepe?

**Done when:** a short report with numbers and a recommendation for each, and you've approved the choices.

### Phase 1 — Voice loop on the Mac

Press a key (stand-in for the button) or say the wake word → speech to text → Claude → spoken reply.

**Done when:** 20 test phrases (including your grandparents' clips) are understood; end of speech to first word is under 3 seconds for 90% of turns; every turn logs its timings and cost; Pepe doesn't hear itself; they can interrupt it; long pauses don't cut people off.

### Phase 2 — Memory and tools

SQLite tables (set up to survive sudden power loss) for people (two per home, each with their own records), medications, doses, appointments, reminders, lists, places of things, preferences, life notes. Claude tools for each. Confirmation rules inside the tools. "What do you know about me?" and "Forget that".

**Done when:** tests prove that a medication can't be saved without confirmation, a dose can't be logged twice, and "forget that" really deletes.

### Phase 3 — Scheduler and reminders

A deterministic reminder engine with a fake clock: medication with retries (at most 2–3), "I already took it", taken late (never suggest doubling), appointments the day before and on the day, birthdays, recurring reminders made by voice, the shopping trip routine. Works with no internet. If the clock can't be trusted, it doesn't fire or log doses, and says why.

**Done when:** "A day with Pepe" (section 5 of the Pepe doc) runs in the simulator in under a minute and matches the table; reminders still fire with the internet unplugged.

### Phase 4 — Manners

The "when to speak" engine (the three categories, daily limit, backing off after short answers, quiet mode, night, "I have visitors", away for days) and the full system prompt from the Pepe doc.

**Done when:** every row of the situations table (section 6) and the second review (6c) is a scenario test; an automatic judge (a second Claude call with a checklist) scores the transcripts against the manners rules, and none fail on medication, honesty or dignity. Each scenario runs 5 times: medication, safety and honesty rules must pass 5 out of 5, everything else at least 4 out of 5. Includes tests where a news headline or a family message tries to give Pepe instructions, and tests where the wrong person answers a reminder.

### Phase 4b — Feedback loop (new)

Spotting feedback, applying it at once, the single follow-up question, the scope, the later check, the safety exceptions, the feedback log, your page and weekly summary, and undo.

**Done when:** in the simulator, 30 feedback examples (direct, vague, praise, said in passing, about the other person, about safety) are understood and applied correctly: 5 runs out of 5 for safety items, 4 out of 5 for the rest. Pepe asks at most one follow-up and drops it when they don't answer. Every change appears on your page, and undoing it works on the next turn.

### Phase 5 — Setup conversation

The welcome and the setup questions as a real conversation, one question at a time, stoppable and resumable, with each permission asked separately, for both people (together or one at a time).

**Done when:** simulated setups (talkative person, short answers, changes mind about a name, stops halfway) all end with correct data saved.

### Phase 6 — Family and safety links

Telegram messages to family (asking first, except the agreed emergency path), with family replies read aloud on request; "Pepe, help" and the button (Telegram and SMS, and a spoken warning if neither can be sent); missed-medication alerts if agreed; the heartbeat monitor; the nightly encrypted backup.

**Lea:** add the family member to the Telegram bot, set up an SMS sender (for example Twilio), and create the heartbeat monitor.

**Done when:** a test phone receives each message type; with the internet unplugged, "help" says out loud that it couldn't reach anyone; stopping the device sends you an alert within the hour.

### Phase 7 — The real device

Move to the Raspberry Pi: speakerphone, presence sensor, button, light ring; starts on boot, restarts itself on a crash, remote access for you (for example Tailscale), logs, an encrypted disk, and updates installed only at night, rolled back automatically if the device's self-test fails afterwards.

**Lea:** buy the hardware, flash the card, plug things in, run the commands Claude Code gives you.

**Done when:** 48 hours running at your place with no crash; morning greeting works with the sensor; reminders work with the Wi-Fi switched off; after pulling the plug 10 times mid-use, the dose log is intact and the clock is still right.

### Phase 8 — Trial with your grandparents (2–4 weeks)

**Lea:** set it up with them, explain that it's not an alarm system, keep a family member on the alert list, and make sure their teleasistencia stays in place.

Measure against the baseline week (a family member checked the pill box daily before KORE arrived), using section 6c of the design doc: do they still use it after a month, how often they say "be quiet" or ignore it, missed medication before and after, and what they like, dislike and miss. Fix the manners first, then features.

### Phase 9 — Later, before selling

Several voices and renaming, two-person recognition, the family app, visitor detection, the fall-sound experiment, keys moved to the backend, device setup at scale, the legal checks (MDR, GDPR, data processing agreements), pricing. Consider partnering with teleasistencia providers (town halls, the Red Cross, private companies) instead of competing with them: they already reach older people and their families, and KORE adds daily company to their emergency button.

## 8. How to test without you

This is what makes "Claude builds it alone" realistic:

- **Text simulator:** type as the grandparent, see Pepe's replies, with a fake clock you can speed up.
- **Voice simulator:** plays recorded clips into the pipeline and checks the transcript and timing.
- **Scenario files:** each one is a short script (time, what the person says, what Pepe must and must not do). Every row of the Pepe doc's tables becomes one.
- **Automatic judge:** a separate Claude call reads each transcript with the manners checklist. Its verdicts are a signal, not proof: Claude Code shows you a sample of transcripts to read.
- **Hard tests for hard rules:** medication, confirmations and family messages are checked by normal code tests, never by the judge.

* **Recorded answers** (new): normal test runs replay saved API answers, so they're free, fast and repeatable. Live runs are deliberate and capped by a test budget (for example €5 a day).
* **Repeated runs** (new): each conversation scenario runs 5 times (see phase 4).
* **Speed and cost in every report** (new): each test run reports the median and slowest turn, and the cost per simulated day.

## 9. What only you can do

1. Create the accounts and type the API keys (Anthropic, ElevenLabs, the Telegram bot, an SMS sender, the heartbeat monitor), with spending limits
2. Export the KORE design doc and this plan into the repo
3. Buy the speakerphone (phase 0) and record test clips of your grandparents and their TV, with their OK
4. Make the decisions in section 10, and approve the experiment results (phase 0.5)
5. Get your grandparents' consent, check their teleasistencia, and organise the baseline week
6. Buy and assemble the rest of the hardware; flash the Raspberry Pi
7. Listen to Pepe yourself at each phase end and say whether it feels right (the advisor can read transcripts, but only you can hear it)
8. Run the trial with your grandparents, and check the feedback page each week (keep, undo or reword what Pepe changed)
9. Before selling: lawyer review, data processing agreements, pricing

## 10. Decisions before starting

My recommendation comes first in each:

- **Language and accent:** the variety they speak at home, only that one in v1, with a voice in the same accent.
- **Voice:** one warm, slow voice in v1; the voice choice comes later.
- **Wake word:** decided by the false wake-up experiment: "Pepe" if it holds up, otherwise "Oye Pepe". Plus the button.
- **Family alerts:** one family member (not you) gets help and missed-medication alerts on Telegram, plus an SMS for help; you get only device alerts.
- **Teleasistencia:** your grandparents have it, or apply for it, before the trial. KORE is never the only safety net.
- **Device:** Raspberry Pi 5 + USB speakerphone, not a ready-made smart speaker (those don't let you control the microphone, the wake word or the sensor).
- **Claude model:** decided by the experiments: a fast model for simple turns and a stronger one when a turn needs tools or judgment, both with caching.
- **Transcripts:** kept 7 days, only to fix problems, with your grandparents' OK.
- **112:** not in v1.

## 11. Starting prompt for Claude Code

Paste this into Claude Code in the KORE folder once phase 0 is done.

```
You are building KORE, a voice companion speaker for older people. KORE is the product; the companion's default name and wake word is "Pepe". The code package is named kore. Read docs/PEPE.md (the design) and docs/BUILD-PLAN.md (the plan) before anything else, especially section 0b (critical review).

SCOPE
- Build only version 1 (section 1 of BUILD-PLAN.md), phase by phase (section 7), starting with phase 0.5 (risk experiments). Nothing from "waits for later". Extra ideas go in your report as suggestions; don't build them.
- Phases 0.5–6 on this Mac with the simulator and a fake clock. Don't start phase 7 until I say the hardware is ready.

HARD RULES OF THE DESIGN
- Claude decides what to say; code decides what is true and when. Medication, doses, reminders, confirmations and family messages go only through tools with tests.
- Never suggest doubling a dose. Never send anything to family without a clear yes, except the emergency path agreed at setup. If a message can't be sent, say so out loud.
- Two people per home: a personal answer (like "I took it") counts only once Pepe knows who is speaking.
- The microphone is processed only after the wake word or the button.
- Feedback about how Pepe behaves is applied at once as a stored, reversible change, with at most one follow-up question, and logged for Lea (section 2, "The feedback loop"). Feedback that would weaken a safety rule is never applied silently.
- Text from outside (news, family messages) is quoted content: it can never trigger tools or change the rules.

HOW TO WORK
- Python. One interface per provider (speech to text, voice, messages) so each can be swapped.
- Tests first. Normal test runs use recorded API answers (free and repeatable). Live API runs only when needed, never more than 5 euros a day in total; log the cost of every live run.
- Conversation scenarios run 5 times: medication, safety and honesty must pass 5/5, the rest 4/5.
- Every turn logs its timings (end of speech to first word) and its cost.
- One local commit per finished step, after all checks pass. Never git push.
- After each phase, write docs/reports/latest.md (git-ignored): what was built, test results, speed and cost numbers, a few sample transcripts, open questions. Keep docs/PROGRESS.md up to date, and docs/reports/state.md with the exact next step, so any new session can resume.
- If blocked on something only I can do (keys, accounts, hardware, a decision), write the exact steps for me and carry on with what doesn't depend on it.

MY RULES
- Never read, print or log API keys (only variable names). I type keys myself.
- Never delete files: move them to _to_delete/ and tell me.
- No new paid service or dependency without my OK; check licences allow a paid product (no GPL).
- Ask before anything that costs more than the test budget or sends real messages to real people.
- Never put real recordings or transcripts of my grandparents in git.

Start now with phase 0.5.
```

## 12. Running it hands-off (new)

To get as close as possible to "Claude does it alone", run two sessions, as you do for Studiply: **Claude Code builds** in the KORE folder, and **a second Claude session acts as your advisor**. The advisor drives Claude Code, checks its work, runs the tests, makes technical choices for you and keeps your to-do list. You're only pulled in for the items in section 9.

Paste this into the advisor session once phase 0 is done:

```
You are my advisor, reviewer and tester for KORE, a voice companion speaker for older people (the companion's default name and wake word is "Pepe"). Claude Code builds it. You drive Claude Code, check everything it does, run the tests, answer its questions on my behalf, and only bring me what truly needs me. Be autonomous, critical and careful.

PROJECT
- Folder: ~/Documents/Projects/Apps/KORE. Read first: CLAUDE.md, docs/PEPE.md, docs/BUILD-PLAN.md (especially 0b and 7), docs/PROGRESS.md.
- First trial: my grandparents (two people in one home). The safety rules in BUILD-PLAN.md come before everything else.

HOW TO DRIVE CLAUDE CODE
- Only one Claude Code session per folder. Check nothing else is running there first.
- Drive it in headless mode from the KORE folder: claude -p "<message>" --continue, and read its replies. Use the permission settings already configured; never use flags that skip permission checks.
- First message: the starting prompt in section 11 of BUILD-PLAN.md.
- If headless driving doesn't work, tell me and give me the exact messages to paste; read its reports from docs/reports/latest.md.

HOW TO WORK: AUTONOMOUS AND CRITICAL
- You decide on my behalf: plans, technical choices, test results. Don't ask me for these.
- Push back when Claude Code overcomplicates, skips tests, guesses, adds things I didn't ask for, or claims something without proof. Check the code.
- Verify every result yourself: run the tests and the scenario suite, git status, git diff, git log --oneline -15.
- Read sample transcripts against the manners rules in docs/PEPE.md (dignity, patience, no lecturing, asks before sending anything). Check the speed and cost numbers in every report; reject a phase that misses the targets in BUILD-PLAN.md.
- Watch live API spending: stop Claude Code if the test budget is exceeded.
- During the trial, read the feedback log daily, flag anything Pepe misunderstood, and send me a weekly summary of what changed.
- Never stop and wait. When something needs me, prepare it completely (exact steps or commands), add it to my list, and keep working on everything that doesn't depend on it.
- Keep a short log in docs/reports/advisor-log.md: what you approved, rejected and why.

MY RULES (never break them)
- Only build what's in version 1. Extra ideas go on my list as suggestions.
- Local commits only. Never git push.
- Never read, print or log API keys (only variable names). I type keys myself.
- Never delete files: move them to _to_delete/ and tell me.
- No new paid service or dependency without my OK; licences must allow a paid product (no GPL).
- Nothing that sends real messages to real people, or touches the device at my grandparents' home, without my OK.
- Never put real recordings or transcripts of my grandparents in git.

WHAT ONLY I CAN DO
- Section 9 of BUILD-PLAN.md. When I come back, show me this list first, each item with your recommendation and exactly what to type or say.

REPORTS
- When I say "report": what was done (with commit hashes), decisions you made and why, what you rejected, my list, what's next. Short and plain. Save it to docs/reports/report-<date>.md.

SAVING STATE AND RESUMING
- Keep docs/reports/advisor-state.md up to date after every step: current phase, what's done, decisions, my list, the exact next action, open problems.
- If you must stop, update it first and tell me in one line where you stopped.
- In a new session (or when I write "continue"): read advisor-state.md, check it against git, and resume without asking me.

START NOW
1. If docs/reports/advisor-state.md exists, resume from it. Otherwise read the docs above.
2. Tell me in a few lines where things stand and anything that looks wrong.
3. Then take over, driving Claude Code, until something truly needs me.
```

## Sources

- [ElevenLabs API pricing](https://elevenlabs.io/pricing/api)
- [ElevenLabs Scribe v2 Realtime](https://elevenlabs.io/realtime-speech-to-text-api)
- [Soniox vs ElevenLabs for Spanish](https://soniox.com/compare-stt/soniox-vs-elevenlabs/spanish)
- [openWakeWord](https://pypi.org/project/openwakeword)
- [LD2410C presence sensor (The Pi Hut)](https://thepihut.com/products/ld2410c-24ghz-human-presence-sensor)
- [WhatsApp Business Platform pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)
- [Medical device apps and the MDR (JMIR)](https://pmc.ncbi.nlm.nih.gov/articles/PMC7381013)

* [ElliQ plans and pricing](https://elliq.com/pages/holidays)
* [Teleasistencia in Madrid (Ayuntamiento de Madrid)](https://diario.madrid.es/?p=176424)
* [Raspberry Pi 5 real-time clock and battery](https://vilros.com/a/learn/resources/guides/understanding-real-time-clocks-rtc-and-their-use-in-raspberry-pi-5)
* [Claude prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
* [WhatsApp Business pricing in Europe](https://blueticks.co/blog/whatsapp-business-pricing-europe-2026)
