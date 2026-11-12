# KORE — The community (plan)

Written 2026-11-12, after Lea's idea: "it needs to be able to send messages directly and it needs to work like a
network… you could only join by recommendation… to create a community… and they have forums". **Status: Lea's
four choices are agreed (2026-11-12), section 9. The plan waits for her go-ahead to build.**

## 1. The idea in one paragraph

People who have a KORE can join a private community, only if a member recommends them and Lea (or a moderator)
approves. Inside it they can **send messages directly to other members** and **talk in forums** (groups by topic:
"Cocina", "Fútbol", "Recuerdos de juventud", their town). Everything works **by voice** (KORE reads new posts and
messages aloud and takes dictated replies) **and on the large-text web page**. It exists for one reason: company.
Loneliness is the first problem KORE is built for (PEPE.md §1), and talking with people of their own age is the
best cure KORE can offer, better than talking to KORE.

## 2. Who is in it

- **Members are the older people who have a KORE** (Lea's choice). Families keep their own family app and are not
  members.
- **Joining:** a member recommends someone ("Quiero invitar a mi amiga Pilar"). Lea approves each new member in her
  console before they can see or write anything. The new member says yes to the community's rules by voice, at
  setup or later.
- **Each member chooses what others see:** the name they want to be called (and only that by default), and
  optionally their town and interests. Never health, medicines, address, phone number or family.

## 3. What a member can do

| By voice | On the web page |
|---|---|
| "¿Tengo mensajes de la comunidad?" → KORE reads them, as quotes | A messages page, large text |
| "Dile a Pilar que mañana la llamo" → read-back word for word → yes → sent | Write a message; a "Enviar" that asks for the yes |
| "¿Qué hay de nuevo en el grupo de cocina?" → the newest posts, three at a time | The forums, by topic |
| "Pon en el grupo de cocina: ¿alguien tiene la receta de las torrijas?" → read-back → yes → posted | Write a post |
| "Quiero invitar a mi amiga Pilar" → an invitation Lea approves | Invite someone |
| "No quiero más mensajes de Fulano" → blocked at once, no question | Block, report |
| "Sal de la comunidad" → one confirmation → gone, their posts removed | The same |

KORE mentions new messages once, when it fits (never at night, never in front of visitors), as it does with family
messages today. It never reads someone else's private message aloud to the wrong person (the two at home are told
apart as today).

## 4. Safety (the same rules, applied to a community)

- **Every message and post is outside text**, like family messages: quoted, never an instruction to KORE, and the
  turn that reads one changes nothing (SR-08).
- **Nothing is sent or posted without a read-back and a clear yes** on the next turn (SR-06).
- **Money and scams:** a message that asks for money, codes, bank details, a transfer, Bizum, or to move the chat to
  another app is marked by code before KORE reads it. KORE reads it with a calm warning ("Ojo: este mensaje pide
  dinero. Antes de nada, coméntelo con su familia.") and the message is shown to Lea (SR-09). KORE never repeats
  codes or numbers from a message.
- **Romance and pressure:** a new friendship is fine (training base MND-091); only requests for money, secrets or
  isolation ("no se lo digas a tu familia") are warned about.
- **Blocking is instant** and needs no question. **Reporting** sends the message to Lea's console, never to the
  other member.
- **Moderation:** Lea (later, trusted moderators) sees reports, can hide a post, pause or remove a member. Every
  action is logged.
- **No health advice between members goes unmarked:** a post or message about medicines is read with "solo su
  médico puede decidir" (as with family messages).
- **The person stays in control:** leaving deletes their posts and messages; "olvídalo" works as everywhere.
- **Privacy:** members see only the profile the person chose. Nothing from the home (medicines, conversations with
  KORE, the calendar) ever reaches the community.

## 5. How it is built

- **A community server**, separate from the boxes. It holds members, invitations, messages, forums, reports and
  blocks. Lea's choice: it will run on her future GPU server; until then it runs on her Mac for building and testing.
  Free software only (licences that allow selling).
- **Each box connects out to the server** (never the other way), over Tailscale at first, like the updates. The box
  keeps working fully if the server is down: KORE just says the community can't be reached right now.
- **Part 1 gets one channel and a few tools**, like the family channel today: `read_community` (outside text),
  `propose_community_message`, `propose_post`, `follow_topic`, `propose_invite`, `block_member`, `report_message`,
  `propose_leave_community`. Sending goes through the same propose → read-back → yes → confirm path.
- **The web page** gets two pages (Comunidad: forums; Mensajes: direct messages) in the same large-text style, and
  Lea's console gets a Moderation page.
- **The training base** gets a new area, `COM`, with situations for all of the above (outside text, scams, a member
  who insists, blocking, two people at home, at night, offline).

## 6. Steps, each with "done when"

1. **C1, the server and members** (about 2 days): accounts, invitations, Lea's approval, profiles, block and
   report. *Done when:* two test boxes on the Mac can be invited, approved and see each other's chosen name.
2. **C2, direct messages** (about 2 days): send by voice and web with read-back and yes; read aloud as quotes; scam
   marks; offline handling. *Done when:* a message dictated on one box is read aloud on the other, and a money
   request is warned about, in tests and in a live check.
3. **C3, forums** (about 3 days): topics, following, posting, "what's new" by voice three at a time, the web pages.
   *Done when:* a post made by voice appears on the web and is read aloud on another box.
4. **C4, moderation** (about 1 day): Lea's console page, hiding posts, pausing members, the log.
5. **C5, the training base** (about 1 day): the `COM` area, 60–80 situations.

About **2 weeks** of work in all, tested at each step. Nothing reaches a real person until Lea says so.

## 7. What it costs

Nothing per month on Lea's own server. Live checks with Claude during building: a few euros in total, within the
daily cap.

## 8. Open questions for Lea (none blocks C1)

- The rules members agree to when they join (I'll draft them in plain Spanish for her to edit).
- Forum topics to start with, and whether each town gets its own group.
- Can members also call each other later (needs the phone link, CALL area)?
- Moderators besides Lea.

## 9. Decisions (Lea, 2026-11-12)

1. Members: **the older people who have a KORE**. Families stay in their family app.
2. Use: **both voice and the web page**.
3. Joining: **a member recommends; Lea (or a moderator) approves** each new member.
4. Server: **Lea's future GPU server**; built and tested on her Mac until then.
