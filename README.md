# KORE

![Status: in progress](https://img.shields.io/badge/status-in%20progress-orange)

**Work in progress:** I'm building and testing version 1 right now.

KORE is a voice companion for older people who speak Spanish. It's made for people who don't use apps or phones easily: you just talk to it. It keeps them company, reminds them about their medicines and appointments, helps with the shopping list, shares photos and messages with their family, and can get help if something is wrong.

The code is private while I test it in a real home.

## My main design rule

The AI only decides what to say. Anything that has to be correct, like medicines, doses, reminders or messages to the family, is handled by normal code that I test. This way the AI can't make up a dose or send a message to the family without a clear yes. If a message can't be sent, KORE says it out loud.

## What it does

- Talks in Spanish, using *usted*, one question at a time, and says times the way people say them ("a las nueve y diez")
- Medication reminders and confirmations, with safety rules like never suggesting a double dose
- Calendar, shopping list, weather and news
- Photos and messages with the family (Telegram and SMS), plus an app for relatives
- A help button that works even if the AI doesn't
- A simple home screen with big text

## Choosing its voice

I tested three Claude models with the same conversations: someone feeling dizzy, a scam phone call, a fake message about a medicine, and "cállate".

| Model | Time to first sentence (median / slowest) | Cost per answer | What I saw |
|---|---|---|---|
| Opus 5.5 | 2.04 s / 8.01 s | $0.0042 | Very good, but too slow to talk |
| **Sonnet 5.5** | 1.34 s / 1.60 s | $0.0019 | **The best:** warm, asks before sending anything, didn't fall for the scam |
| Haiku 5.5 | 0.77 s / 1.33 s | $0.0001 | Safe, but asked three questions at once and read "09:10" as numbers |

All three refused to suggest a double dose, spotted the scam and said they were a machine. I chose Sonnet. After improving the prompt, it starts answering in about 1 second, and on my Mac a whole turn takes about 2.7 s from when you stop talking to when it starts speaking.

## Next: its own model

Right now KORE uses Claude. My goal is to train an open-source model (Qwen or Gemma) only for KORE, running on the device itself. That way conversations stay at home, there's no cost per answer, and it doesn't depend on one company. It will only replace Claude once it passes the same tests, and anything about medicines or safety has to pass 5 times out of 5.

## Built with

Python, Claude API, Apple speech recognition, Raspberry Pi 5, Telegram and SMS
