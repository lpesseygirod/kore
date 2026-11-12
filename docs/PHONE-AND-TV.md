# Connecting Pepe to their phone and their TV: what's possible, and what it needs

What they asked for: connect Pepe to apps on their phone ("look for a photo of me", "when did I last call Marisa",
"can you call…") and to the TV ("what did I watch yesterday").

Most of this is beyond version 1 (BUILD-PLAN section 1) and would need a phone app, new services, or changes to the
privacy promises. These are the options and what I think of each.

## 1. A phone app is the first step for anything on the phone

**Decided: the web app is enough.** No phone app. Carmen and Antonio use the web app in their
phone's browser. What only an app could do (search their photos, read their calls) stays out. Calls work through
the "Llamar" button, and photos through the family app and "Fotos".
**Built: Pepe's icon on the home screen** (`kore/web/icons.py`). A pomegranate on marble, drawn by code, plus a web
app manifest. "Añadir a pantalla de inicio" on an iPhone or Android phone, or "Install" in Chrome or Edge on Windows,
puts Pepe there as an app of its own. Ajustes explains how, in big text. On an iPhone the icon keeps its own login, so
the code is entered once more inside it.

The home page (the web) can't reach the phone's photos library, call history or contacts. A real app can, with the
person's permission, and only partly:

| They ask | iPhone | Android | Recommendation |
| --- | --- | --- | --- |
| "Llama a Marisa" | The app opens the call with Marisa's number; they press call | The same (or a one-tap call if the app is allowed to place calls) | Yes. Already possible on the web page today with a "Llamar" button (a phone link), no app needed |
| "¿Cuándo llamé a Marisa por última vez?" | **Not possible**: Apple lets no app read the call history | Only an app that is the phone's main dialer may read it (Google's rules) | Not worth it. Instead Pepe can remember calls it starts ("Llamaste a Marisa el martes") |
| "Busca una foto mía con…" | An app may read the photos they allow; Apple keeps the names of faces to itself | The app may read photos; no face names either | Partly: the photos they choose to share with Pepe (the family album), and Claude looking at a few. Searching the whole library by person isn't possible, and sending all their photos to Claude would be costly and a big privacy step |
| Video call | WhatsApp has no official way for other apps to start calls | Same | A WhatsApp link opens the chat with that person; they press video. That works today |

What an app would cost: building it for iPhone and Android (or one shared code base, such as React Native or Flutter), an
Apple developer account (99 USD a year) and Google Play (25 USD once), store reviews, and phone notifications needing
the box to be reachable from outside the home (a relay: WEB-PLAN decision 1 kept it home Wi-Fi only).

## 1b. A Windows computer

The pages are plain web pages, so they already work in any browser on Windows (Edge, Chrome), on the home Wi-Fi.
Two small steps would make it feel like an app, at no cost:
- **"Install as an app"** from Edge or Chrome (a web app manifest and an icon on the box): it gets its own window and a
  Start-menu icon. Built, with the phone icon above.
- **A real Windows program** (with a store listing): only worth it if they need something a browser can't do,
  such as reading files on the computer. Not recommended for now.
What a computer still can't give: the phone's call history or WhatsApp, and Pepe's voice from the computer's
microphone ("Pulsa y habla") needs the same decision as on the phone (speech services in the page).

## 1c. Messages by WhatsApp

Today Pepe's messages to family go by Telegram, help also by SMS, and the family app (BUILD-PLAN: "v1 uses
Telegram and SMS, not WhatsApp"). Every channel sits behind one interface (`kore/family/channels.py`), so WhatsApp
can be added as one more without touching the rest. The ways to do it:

| Way | How it works | Cost and limits | Recommendation |
| --- | --- | --- | --- |
| **WhatsApp Business Platform (Meta's Cloud API)**, directly or through Twilio | The box gets its own WhatsApp number ("Pepe de Carmen y Antonio"). Family receive and answer there | A Meta business account and a phone number just for it. Messages Pepe starts must use templates Meta approves, and are paid per message (a few cents each in Spain; replies within 24 h of the family writing are free). Twilio adds its own fee | **Yes, after the trial**, if the family prefers WhatsApp. It's a new paid service and a change to the plan |
| Automating Carmen's own WhatsApp (unofficial tools that drive WhatsApp Web) | Messages would come from her own number | Against WhatsApp's terms: numbers get banned without warning | **No.** Never for help alerts |
| A WhatsApp link on the page | Opens WhatsApp on her phone with that person; she writes or calls herself | Free, nothing automated | Already built (Familia → Videollamada) |

For help alerts, reliability comes first: keep Telegram and SMS as the help path even if WhatsApp is added, since a
WhatsApp template or account problem must never stop "Pepe, ayuda".

### Decided: built, switched off

Built in `kore/family/channels.py` (`TwilioWhatsApp`) and `kore/family/messages.py`, tested with fake accounts
(`tests/test_whatsapp.py`). **Off** until I set `TWILIO_WHATSAPP_FROM` and `TWILIO_WHATSAPP_TEMPLATE`: nothing
changes for the trial before that.

- **Through Twilio, not Meta directly.** Meta's own service delivers the family's replies only to a web address
  reachable from the internet, and the box stays on the home Wi-Fi (WEB-PLAN decision 1). Twilio keeps the replies
  and the box asks for them every few seconds, as it does with Telegram. It is also the account the SMS already uses.
- **Linking:** a family member sends the usual pairing code (`python -m kore.family pair --contact N`) to Pepe's
  WhatsApp number. That message is also their agreement to get messages there, which Meta requires.
- **Once linked:** Pepe's messages to that person (the ones Carmen and Antonio dictate, and the missed-medicine
  alerts they agreed to) go by WhatsApp instead of Telegram. Their replies are kept and read like Telegram ones.
- **Help goes every way they have:** Telegram, WhatsApp and SMS. If WhatsApp is down, help still goes by the other two.
- **Within 24 hours of their last message:** plain text. **After that:** WhatsApp only takes the approved template,
  with the message as its one variable.
- **Failures after acceptance:** Twilio can accept a message and fail it seconds later, for example when the number
  has no WhatsApp or the template was rejected. The box checks for the next 6 hours.
  - A message or alert that didn't arrive goes again by Telegram when the person has it.
  - If nothing got through in the end, it is said out loud as "no se ha podido enviar", including a call for help
    that seemed sent by WhatsApp alone. I get a note.
  - "Al final sí se ha enviado" is said only once WhatsApp confirms it arrived.
- **Not by WhatsApp:**
  - Photos: Twilio needs them at a public web address, and the box has none. Photos keep going through the family
    app and Telegram.
  - My own device messages and backups stay on Telegram.

**To switch it on (paid; real messages to real people):**
1. Twilio console → Messaging → Senders → WhatsApp senders: register a sender. This connects a Meta business account
   ("Meta Business Portfolio"; Meta may ask to verify the business). It needs a phone number not already on WhatsApp;
   a Twilio number works.
2. Twilio → Content Template Builder: a template in Spanish, category **Utility**, with one variable. Suggested body:
   `Mensaje de Pepe, el asistente de casa de Carmen y Antonio: {{1}}`. Submit it for WhatsApp approval, which usually
   takes minutes to a day. Meta may class it as marketing, which is dearer; if so, split it into
   separate templates (help, medicine, messages) in the code.
3. Type in `.env`: `TWILIO_WHATSAPP_FROM=+34…` (the sender) and `TWILIO_WHATSAPP_TEMPLATE=HX…` (the template's id).
4. Link my own phone first, as a test contact:
   `uv run --env-file .env python -m kore.family --db data/test-family.db pair --contact 1`, then send the code to
   Pepe's WhatsApp number from my phone.
5. Check: `uv run --env-file .env python -m kore.family --db data/test-family.db test-send 1 --yes --whatsapp`.
   It says whether WhatsApp failed the message afterwards.

Cost: Twilio's fee per WhatsApp message plus Meta's charge per template message (a few cents in Spain; check
Meta's current price list). Plain replies within 24 hours have no Meta charge. A home sends a handful a day, so a few euros a month.

## 2. The TV

| Way | What Pepe could know | Privacy and cost | Recommendation |
| --- | --- | --- | --- |
| Ask them, or note what they tell Pepe ("estuve viendo el concurso") | What they mention | None | Already works (notes) |
| The TV guide: "¿qué echaban ayer en La 1 a las diez?" | The programme at a time on a channel | A web look-up (WEB-PLAN decision 7 is still open), free guides exist | Good second step, once I decide on look-ups |
| The smart TV or the decoder's own connection (Samsung SmartThings, LG webOS, Google TV, Movistar Plus+…) | Which channel or app is on, sometimes its history | Depends on their exact TV; each brand is a separate piece of work; some need accounts | Only after seeing their TV model |
| The cable between the box and the TV (HDMI-CEC) | That the TV is on and which input | Nothing leaves the home | Small, but says little |
| Listening to the TV sound to recognise programmes | What is playing | **Breaks the promise "Pepe listens only after its name"** (BUILD-PLAN rule and the website) | No |

## Open questions

1. ~~A phone app~~ Decided: no, the web app is enough. "Install as an app" on Windows works with the same icon.
2. "Llama a…" from the web page with a call button, and Pepe noting the calls it starts: OK? (Free, no app.)
3. Family photos: shared on purpose (family app → Carmen's Fotos), not by searching their phone: OK?
4. TV: what TV and decoder do they have? And may Pepe look things up on the internet (the TV guide)?
5. ~~WhatsApp~~ Decided: built, switched off (section 1c), with the steps to switch it on.
