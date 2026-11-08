# Connecting Pepe to their phone and their TV: what's possible, and what it needs

Lea, 2026-11-08: "they want to connect it maybe to apps in their phone… look for a photo of me…, when did I last
call Marisa, can you call…, and what did I watch on TV yesterday, so access to the TV too."

None of this is built. It is beyond version 1 (BUILD-PLAN section 1), and most of it needs things only Lea can
decide: a phone app instead of the web page, new services, and changes to privacy promises. These are the options,
with Claude's recommendation for each.

## 1. A phone app is the first step for anything on the phone

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
  Start-menu icon. Recommended when they want it; about a day's work, no new service.
- **A real Windows program** (with a store listing): only worth it if they need something a browser can't do,
  such as reading files on the computer. Not recommended for now.
What a computer still can't give: the phone's call history or WhatsApp, and Pepe's voice from the computer's
microphone ("Pulsa y habla") needs the same decision as on the phone (speech services in the page).

## 1c. Messages by WhatsApp (Lea, 2026-11-08: "for the send messages, connect it to WhatsApp")

Today Pepe's messages to family go by Telegram, help also by SMS, and the family app (BUILD-PLAN: "v1 uses
Telegram and SMS, not WhatsApp"). Every channel sits behind one interface (`kore/family/channels.py`), so WhatsApp
can be added as one more without touching the rest. The ways to do it:

| Way | How it works | Cost and limits | Recommendation |
| --- | --- | --- | --- |
| **WhatsApp Business Platform (Meta's Cloud API)**, directly or through Twilio | The box gets its own WhatsApp number ("Pepe de Carmen y Antonio"). Family receive and answer there | A Meta business account and a phone number just for it. Messages Pepe starts must use templates Meta approves, and are paid per message (a few cents each in Spain; replies within 24 h of the family writing are free). Twilio adds its own fee | **Yes, after the trial**, if the family prefers WhatsApp. Lea's OK needed (new paid service, a change to the plan) |
| Automating Carmen's own WhatsApp (unofficial tools that drive WhatsApp Web) | Messages would come from her own number | Against WhatsApp's terms: numbers get banned without warning | **No.** Never for help alerts |
| A WhatsApp link on the page | Opens WhatsApp on her phone with that person; she writes or calls herself | Free, nothing automated | Already built (Familia → Videollamada) |

For help alerts, reliability comes first: keep Telegram and SMS as the help path even if WhatsApp is added, since a
WhatsApp template or account problem must never stop "Pepe, ayuda".

## 2. The TV

| Way | What Pepe could know | Privacy and cost | Recommendation |
| --- | --- | --- | --- |
| Ask them, or note what they tell Pepe ("estuve viendo el concurso") | What they mention | None | Already works (notes) |
| The TV guide: "¿qué echaban ayer en La 1 a las diez?" | The programme at a time on a channel | A web look-up (WEB-PLAN decision 7 is still open), free guides exist | Good second step, with Lea's yes on look-ups |
| The smart TV or the decoder's own connection (Samsung SmartThings, LG webOS, Google TV, Movistar Plus+…) | Which channel or app is on, sometimes its history | Depends on their exact TV; each brand is a separate piece of work; some need accounts | Only after seeing their TV model |
| The cable between the box and the TV (HDMI-CEC) | That the TV is on and which input | Nothing leaves the home | Small, but says little |
| Listening to the TV sound to recognise programmes | What is playing | **Breaks the promise "Pepe listens only after its name"** (BUILD-PLAN rule and the website) | No |

## Questions for Lea

1. A phone app (instead of the web page) for Carmen and Antonio: yes, later, or no? It's a bigger project than the
   rest of version 1 together. Do they use a Windows computer? Then "install as an app" from the browser is the
   cheap way to have Pepe there too.
2. "Llama a…" from the web page with a call button, and Pepe noting the calls it starts: OK? (Free, no app.)
3. Family photos: shared on purpose (family app → Carmen's Fotos), not by searching their phone: OK?
4. TV: what TV and decoder do they have? And may Pepe look things up on the internet (the TV guide)?
5. WhatsApp: add Meta's WhatsApp Business service as a message channel (a number for the box, a few cents per
   message), keeping Telegram and SMS for help? Now, after the trial, or not?
