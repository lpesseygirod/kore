# Building the design: Lea's console, the family app, the website

Source: Lea's design canvas "KORE — Architecture & Web Design" (claude.ai artifact, 2026-11-08). A copy of every
screen is in `docs/design/*.dc.html` (reference only, not served).

## What the design holds, and where it goes

| Design | What it is | Where in the build |
| --- | --- | --- |
| Architecture board | Device core, cloud APIs, people | Matches what is built and planned (BUILD-PLAN). No change. |
| Style | Wine #6E1E35, linen ground, Inter, big type, 44 px targets, status pills | Every web page, the grandparents' pages too |
| Console 1–7 (English) | Lea's view: Overview, Calendar, Chat, Feedback, People & memory, Device, Settings | Step W5 ("the operator view"), built from these screens |
| Family app 1–4 | Home, Calendar, Messages, Alerts on a phone | Replaces the W4 family pages, same rules |
| Public website | How it works, promises, price, waitlist | A static page in `site/`, not on the box |

The grandparents' own pages (Hoy, Hablar, Calendario, La compra, Sobre mí) stay: the design doesn't show them, and
nothing in it removes them. They get the design's look.

## Decisions taken (Claude's recommendations, under Lea's "use your recommendations")

1. **Privacy in Lea's console stays consent-based** (WEB-PLAN decision 5). Two new permissions, asked by Pepe at
   setup, one per person, default no:
   - `lea_sees_day`: "¿Puede Lea ver sus recordatorios, medicinas, notas y lo que le dice a Pepe sobre cómo
     hablar, para cuidar de que Pepe funcione bien?" Gates Overview's doses, Calendar, People & memory, Feedback.
   - `lea_reads_conversations`: "¿Puede Lea leer sus conversaciones de los últimos 7 días, solo para arreglar
     fallos?" Gates the Chat page.
   Without them the page says so ("Carmen hasn't agreed to this") and shows only device health. The Settings page
   shows every permission read-only: only they change them, by voice.
2. **Seven days for Lea, ninety for them.** Lea's Chat page shows only the last 7 days. Their own history on their
   page keeps the 90 days decided in W2. (The design's Settings says "deleted after 7 days": to confirm with Lea.)
3. **Language:** Lea's console in English, as designed. The family app in Spanish (the family is Spanish).
4. **Family additions are still proposals** the person confirms (hard rule: family text is outside text). The
   family calendar says "Pepe se lo dirá y lo apuntará cuando digan que sí".
5. **Family sees medicines only with a new permission** `family_sees_doses` per person ("Not shared" otherwise).
6. **Lea's "Propose a change" to a medicine** is stored and shown to the person; it changes nothing until they
   confirm it by voice or on their page with the usual read-back. Pepe never moves a dose on its own.
7. **"Talk to Pepe as a test"** runs on a throwaway copy of the database: it never reaches the device or their
   memory. It uses the real Claude only with the key set, inside the web's daily share.
8. **Device page:** shows what exists now (connection heartbeat, clock, backup, speed and cost from the logs).
   Wake word, self-test, updates and restart arrive with phase 7 (the device); until then the page says so.
9. **Website:** static, in `site/`. The waitlist form needs a place to send emails (a form service is a new
   service: Lea's OK first), so until then it is shown but not connected. Price and dates stay as placeholders.
10. **Fonts:** Inter when the phone has it, else the system font. Nothing is loaded from outside (the pages' rule).

## Steps

| Step | What | Done when |
| --- | --- | --- |
| D1 | The look on every page (shared CSS from the Style board) | All current tests pass; pages match the palette and sizes |
| D2 | Console: Overview, Device, Settings (status-only parts first), then Calendar, People & memory, Feedback, Chat behind the permissions | Lea sees nothing private without the person's yes; with it, the pages match the design |
| D3 | Family app: Home, Calendar, Messages, Alerts | Family sees only what was agreed; additions are proposals |
| D4 | Website in `site/` | Opens offline, phone width works, form not connected |
| D5 | Fresh security review of D1–D4, fixes, docs | Findings fixed with tests |
