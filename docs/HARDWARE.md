# KORE — What to buy to build one box

Written 2026-11-07. It follows BUILD-PLAN section 3 and adds what the web plan (WEB-PLAN.md) needs. Prices are
rough Spanish prices as I know them; check them before buying. One box costs about €250–330.

## Buy now: the speakerphone (phases 0.5 and 1 are waiting for it)

| Part | Why | About |
| --- | --- | --- |
| **A USB conference speakerphone with echo cancellation**: Jabra Speak 410/510, or Anker PowerConf S3 | The most important part. It lets Pepe hear quiet voices without hearing itself. Check it's loud enough for them | €80–120 |

It works on your Mac first, so the speed and Spanish-voice experiments can start before anything else arrives.

## Buy before phase 7 (the real device)

| Part | Why | About |
| --- | --- | --- |
| **Raspberry Pi 5, 8 GB** | The box's computer: memory, reminders, the web page | €90 |
| Official Raspberry Pi 27 W USB-C power supply | Weaker chargers cause random crashes | €13 |
| Active cooler for the Pi 5 | It runs all day | €6 |
| **RTC battery** for the Pi 5 (the small official one) | Keeps the clock right through power cuts, so medicine reminders stay on time | €6 |
| **An M.2 SSD board (HAT) and a 256 GB NVMe SSD** | The memory lives here. Memory cards wear out and get corrupted after power cuts | €35–45 |
| A case that fits the Pi with the SSD board | The official case doesn't fit the SSD board: check before buying | €10–15 |
| A 32 GB microSD card | Only to start it up the first time | €8 |
| **LD2410C presence sensor** (mmWave) and a few jumper wires | Knows when someone is up or sitting still, for the morning greeting | €8 |
| **A large arcade button** (60–100 mm) | The help button, and "talk to Pepe" without the wake word | €8 |
| A small LED ring (12–16 LEDs, WS2812 type) with wires already attached | Shows listening, speaking, quiet mode, a reminder waiting | €8 |
| A printed sticker with the box's QR code | For the web page (I make the image) | — |

## Recommended

| Part | Why | About |
| --- | --- | --- |
| A small battery backup (a UPS board for the Pi, or a power bank that charges and powers at the same time) | A power cut doesn't stop reminders, and the clock and the database stay safe | €30–50 |
| A 4G USB modem and a data SIM | Only if their Wi-Fi is unreliable: "help" and the heartbeat need internet | €40 + plan |
| A soldering kit | Only if the button or ring come without wires; otherwise not needed | €20 |

## For the web page (only after your decisions in WEB-PLAN.md section 9)

- Nothing more for the box itself.
- A domain of your own (about €12 a year) if you choose a secure connection on the home Wi-Fi (decision 2a).
- A phone or tablet for whoever uses it. An old one is fine.

## Already have, or need nothing extra

- Your Mac: to write the system onto the card and to reach the box from home.
- No screen, keyboard or mouse for the box: you reach it from your Mac.

## A second box

The same list again. Each box gets its own QR sticker and its own memory. A custom case comes later, before
selling (BUILD-PLAN phase 9).

## Putting it together

When the parts arrive, I'll write the steps one at a time, in the order you plug things in, with a check after
each one. You plug things in; I write the commands.
