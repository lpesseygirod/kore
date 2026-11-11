# KORE

**A Spanish-speaking voice companion for older people.** KORE greets them, keeps them company, reminds them of their medication, appointments and shopping, shares photos and messages with their family, and gets help when something is wrong. It is designed for people who will never use an app: you just talk to it.

> The source code is private while KORE is tested in a real home. This page describes the design and what has been measured.

## The rule everything is built around

**The AI decides what to say. Tested code decides what is true.**

Medication, doses, reminders, confirmations and family messages go only through tested tools, never through the language model alone. The model can't invent a dose, can't send anything to the family without a clear "yes", and if a message can't be sent, KORE says so out loud. Text from outside (news, messages) is treated as quoted content: it can never trigger an action or change a rule.

## What it does

- **Conversation** in Spanish, with *usted* by default, one question at a time, and times said the way people say them ("a las nueve y diez").
- **Medication reminders** with confirmations, and safety rules such as never suggesting a double dose.
- **Calendar, shopping list, weather and news.**
- **Family:** photos and messages both ways (Telegram and SMS), and a companion app for relatives.
- **Help button and emergency path**, which never depend on the AI.
- **A simple home screen** with large text for the essentials.

## Choosing the voice: a measured experiment

Three Claude models, the same scripted conversations (a dizzy turn, a scam call, an injected fake message about a medicine, "cállate"):

| Model | First sentence (median / slowest) | Cost per turn | Behaviour |
|---|---|---|---|
| Opus 5.5 | 2.04 s / 8.01 s | $0.0042 | Very good, but too slow for live voice |
| **Sonnet 5.5** | 1.34 s / 1.60 s | $0.0019 | **Best:** warm, asks before sending, firm on the scam and the injected message |
| Haiku 5.5 | 0.77 s / 1.33 s | $0.0001 | Safe but weaker: three questions in one reply, reads "09:10" as digits |

All three refused to suggest doubling a dose, recognised the scam and said they were a machine. Sonnet was chosen. After fixing the prompt, the model's first word arrives in ~0.95 s (median), and on the Mac a full turn takes ~2.7 s from the end of speech to the first spoken word.

## Two parts

| | Part 1: the app | Part 2: KORE's own model |
|---|---|---|
| What | Voice, memory, reminders, family messages, help | An open-source model (Qwen or Gemma) trained only to be KORE |
| Brain | Claude, for now | Runs locally on the device |
| Why | Works today | Conversations never leave the home, no cost per answer, no dependence on one provider |

The local model replaces Claude only once it passes the same tests, run five times each: medication, safety and honesty must pass 5 out of 5.

## Built with

Python · Claude API · Apple speech recognition (Mac) · Raspberry Pi 5 · Telegram and SMS · a web home screen
