"""Lea's console (her design: Console 1–7), in English. Plain HTML, no scripts, everything escaped (pages.e)."""

from __future__ import annotations

from datetime import date, timedelta

from kore.web.pages import e, layout

NAV = [("/", "Overview"), ("/console/calendar", "Calendar"), ("/console/chat", "Chat"),
       ("/console/feedback", "Feedback"), ("/console/people", "People"), ("/console/device", "Device"),
       ("/console/settings", "Settings")]


def _page(title: str, here: str, home: str, body: str, online: bool | None = None) -> str:
    links = "".join(f'<a href="{href}" class="{"here" if href == here else ""}"'
                    f'{" aria-current=page" if href == here else ""}>{e(label)}</a>' for href, label in NAV)
    dot = "" if online is None else f'<span class="dot{"" if online else " off"}"></span>'
    nav = (f'<header class="top"><div class="bar"><a class="mark" href="/">KORE</a>'
           f'<nav aria-label="Main">{links}</nav><span class="home-chip">{dot}{e(home)}</span>'
           f'<form method="post" action="/salir"><button class="small" type="submit">Log out</button></form>'
           f'</div></header>')
    return layout(f"KORE — {title}", body, nav, lang="en", compact=True)


def _notice(note: str, bad: bool = False) -> str:
    return f'<p class="msg{" bad" if bad else " good"}" role="status">{e(note)}</p>' if note else ""


def _not_agreed(what: str) -> str:
    return (f'<p class="msg">Nobody has agreed yet that you can see {e(what)}. Pepe asks each of them at setup '
            "(and they can say yes any time, by voice).</p>")


def _pill(text: str, kind: str = "") -> str:
    return f'<span class="pill {kind}">{e(text)}</span>'


def _greeting(hour: int) -> str:
    return "Good morning" if hour < 13 else "Good afternoon" if hour < 20 else "Good evening"


# --- 1 · Overview ------------------------------------------------------------------------------------


def overview(d: dict) -> str:
    dev, today = d["device"], d["today"]
    loop = dev["loop"]
    device_card = (f'<a class="card stat" href="/console/device"><span class="label">Device</span>'
                   + (f'<span class="big-number" style="color:var(--online)">Online</span><span class="muted">Heartbeat '
                      f'{loop["ago_min"]} min ago</span>' if loop and loop["online"] else
                      f'<span class="big-number" style="color:var(--wait)">{"Offline" if loop else "Not started"}'
                      f'</span><span class="muted">{"Last seen " + str(loop["ago_min"]) + " min ago" if loop else "Start KORE (Pepe.command on the Mac, or the box) to see it here"}</span>')
                   + "</a>")
    doses = (f'{today["taken"]} of {today["total"]} taken' if today["total"] else "None today") if today["anyone"] \
        else "Not shared"
    nxt = f'Next: {e(today["next"][1])}, {e(today["next"][0])}' if today.get("next") else "&nbsp;"
    cards = [device_card,
             f'<a class="card stat" href="/console/calendar"><span class="label">Today’s doses</span>'
             f'<span class="big-number">{e(doses)}</span><span class="muted">{nxt}</span></a>',
             f'<a class="card stat" href="/console/chat"><span class="label">Web messages</span>'
             f'<span class="big-number">{"Not shared" if d["messages_today"] is None else str(d["messages_today"]) + " today"}'
             f'</span><span class="muted">Written to Pepe on their page</span></a>',
             f'<a class="card stat {"hero" if d["needs_you"] else ""}" href="/console/feedback"><span class="label">'
             f'Needs you</span><span class="big-number">{d["needs_you"]} safety item{"s" if d["needs_you"] != 1 else ""}'
             f'</span><span class="muted">Open feedback</span></a>']
    rows = "".join(f'<li><span class="time">{e(i["time"])}</span><span class="what">{e(i["what"])}</span>'
                   f'{_pill(i["label"], i["pill"])}</li>' for i in today["items"])
    today_html = (f'<ul class="plain">{rows}</ul>' if rows else
                  ('<p class="muted">Nothing today.</p>' if today["anyone"] else _not_agreed("their day")))
    week = d["week_numbers"]
    side = "".join(f'<div class="kv"><span>{e(k)}</span><b>{e(v)}</b></div>' for k, v in week.items())
    shop = d.get("shopping")
    shop_html = (f'<section class="card tint"><h2 style="margin-top:0">Shopping list</h2><p>{e(" · ".join(shop))}</p>'
                 f'</section>' if shop else "")
    return _page("Overview", "/", d["home"], f"""
<h1>{_greeting(d["hour"])}, Lea</h1>
<p class="muted">{e(d["date"])}. {e(d["summary"])}</p>
<div class="grid">{"".join(cards)}</div>
<div class="row" style="margin-top:1rem">
<section class="card wide"><div class="kv"><h2 style="margin:0">Today</h2><a href="/console/calendar">Open calendar</a></div>
{today_html}</section>
<div class="side"><section class="card"><h2 style="margin-top:0">This week</h2>{side}</section>{shop_html}</div>
</div>""", online=bool(dev["loop"] and dev["loop"]["online"]))


# --- 2 · Calendar ------------------------------------------------------------------------------------


def calendar(d: dict, note: str = "", bad: bool = False) -> str:
    start: date = d["week"]["start"]
    prev, nxt = start - timedelta(days=7), start + timedelta(days=7)
    end = start + timedelta(days=6)
    cols = []
    for day in d["week"]["days"]:
        evs = "".join(
            f'<div class="ev {ev["kind"]}"><b>{e(ev["time"])}</b> {e(ev["text"])}'
            + (f'<br><span style="color:var(--ok)">{e(ev["note"])}</span>' if ev.get("ok") else
               f'<br><span class="muted">{e(ev["note"])}</span>' if ev.get("note") else "") + "</div>"
            for ev in day["events"])
        label = day["date"].strftime("%a %-d").upper() + (" · TODAY" if day["today"] else "")
        cols.append(f'<div class="day{" today" if day["today"] else ""}"><div class="label"'
                    f'{" style=color:var(--accent)" if day["today"] else ""}>{e(label)}</div>{evs}</div>')
    grid = (f'<div class="week"><div class="days7">{"".join(cols)}</div></div>' if d["week"]["anyone"]
            else _not_agreed("their calendar"))
    meds = "".join(f'<li><span class="what"><b>{e(m["person"])}</b> · {e(m["name"])}'
                   f'{" (" + e(m["description"]) + ")" if m["description"] else ""}</span><span>{e(", ".join(m["times"]))}'
                   f'</span></li>' for m in d["meds"])
    med_options = "".join(f'<option value="{m["id"]}">{e(m["person"])} · {e(m["name"])}</option>' for m in d["meds"])
    who = "".join(f'<option value="{p["id"]}">{e(p["name"])}</option>' for p in d["people"] if p["day"])
    who += '<option value="both">Both</option>' if d["shared_ok"] else ""
    rep = "".join(f"<li>{e(r)}</li>" for r in d["repeating"])
    props = "".join(f'<li><span class="what">{e(p["what"])} · for {e(p["for"])}</span>{_pill(p["status"])}</li>'
                    for p in d["proposals"])
    return _page("Calendar", "/console/calendar", d["home"], f"""
<div class="kv" style="flex-wrap:wrap;align-items:flex-end"><div><h1 style="margin-bottom:.3rem">Calendar</h1>
<p class="muted" style="margin:0">{e(start.strftime("%-d %B"))} – {e(end.strftime("%-d %B"))}. Everything Pepe will
remind them about, and what happened.</p></div>
<div style="display:flex;gap:.4rem;flex-wrap:wrap"><a class="small" href="/console/calendar?week={prev.isoformat()}"
aria-label="Previous week">‹</a><a class="small" href="/console/calendar">This week</a>
<a class="small" href="/console/calendar?week={nxt.isoformat()}" aria-label="Next week">›</a></div></div>
{_notice(note, bad)}
<div class="legend" style="margin:1rem 0"><span><span class="sw" style="background:var(--tint)"></span>Medication</span>
<span><span class="sw" style="background:var(--wait-bg)"></span>Appointment</span>
<span><span class="sw" style="background:var(--accent)"></span>Birthday</span>
<span><span class="sw" style="background:var(--off-bg)"></span>Reminder</span></div>
{grid}
<div class="row" style="margin-top:1.5rem">
<section class="card wide"><h2 style="margin-top:0">Medication schedule</h2>
{f'<ul class="plain">{meds}</ul>' if meds else '<p class="muted">Nothing shared with you.</p>'}
<p class="muted">You can propose a change here. Pepe confirms it with them out loud before it starts, and never moves a
dose on its own.</p>
{f'''<form method="post" action="/console/calendar/medicine" style="display:flex;gap:.6rem;flex-wrap:wrap;align-items:flex-end">
<div style="flex:1 1 200px"><label class="f" for="med">Medicine</label><select class="f" id="med" name="id">{med_options}</select></div>
<div style="flex:1 1 160px"><label class="f" for="times">New times</label><input class="f" id="times" name="times" placeholder="09:00, 21:30" required></div>
<button class="small" type="submit">Propose a change</button></form>''' if med_options else ''}
</section>
<section class="card side"><h2 style="margin-top:0">Repeating &amp; birthdays</h2>
{f'<ul class="plain">{rep}</ul>' if rep else '<p class="muted">Nothing shared with you.</p>'}</section>
</div>
<section class="card"><h2 style="margin-top:0">Add to calendar</h2>
<p class="muted">A proposal: they see it on their page and save it with their own yes.</p>
{f'''<form method="post" action="/console/calendar/add" class="grid" style="align-items:end">
<div><label class="f" for="for">For</label><select class="f" id="for" name="for">{who}</select></div>
<div><label class="f" for="title">What</label><input class="f" id="title" name="title" maxlength="120" required></div>
<div><label class="f" for="day">Day</label><input class="f" type="date" id="day" name="day" required></div>
<div><label class="f" for="time">Time</label><input class="f" type="time" id="time" name="time" required></div>
<div><label class="f" for="where">Where</label><input class="f" id="where" name="where" maxlength="120"></div>
<button class="big" type="submit" style="margin:0">Add to calendar</button></form>''' if who else _not_agreed("their calendar")}
{f'<h2>Your proposals</h2><ul class="plain">{props}</ul>' if props else ''}
</section>""")


# --- 3 · Chat ------------------------------------------------------------------------------------------


def chat(d: dict, note: str = "", bad: bool = False) -> str:
    convs = d["conversations"]
    items, last_day = [], None
    for c in convs:
        day = c["start"].date()
        if day != last_day:
            items.append(f'<div class="label" style="padding:.5rem .6rem">{e(day.strftime("%A %-d %B").upper())}</div>')
            last_day = day
        here = d.get("open") and d["open"]["first"] == c["first"] and d["open"]["person_id"] == c["person_id"]
        items.append(f'<a href="/console/chat?p={c["person_id"]}&amp;c={c["first"]}" class="card" style="display:block;'
                     f'text-decoration:none;color:var(--fg);margin:.3rem 0{";background:var(--tint)" if here else ""}"'
                     f'{" aria-current=true" if here else ""}><b>{e(c["title"])}</b><br><span class="muted">'
                     f'{e(c["start"].strftime("%H:%M"))} · {e(c["person"])}{" · by voice" if c["channel"] == "voice" else ""}'
                     f'</span></a>')
    o = d.get("open")
    if o:
        bubbles = "".join(
            f'<div class="bubble{" me" if r["who"] == "pepe" else ""}">{e(r["text"]) or "[a photo]"}'
            f'<span class="when">{"Pepe" if r["who"] == "pepe" else e(o["person"])}, {e(r["at"].strftime("%H:%M"))}'
            f'{" (voice)" if r["channel"] == "voice" else ""}{" · with a photo" if r["photo"] else ""}</span></div>'
            for r in o["rows"])
        marks = "".join(f'<div class="note-line">Marked: {e(m)}</div>' for m in o.get("marks", []))
        hidden = f'<input type="hidden" name="p" value="{o["person_id"]}"><input type="hidden" name="c" value="{o["first"]}">'
        detail = f"""<section class="card wide"><div class="kv"><h2 style="margin:0">{e(o["title"])} · {e(o["start"].strftime("%H:%M"))}</h2>
<span class="muted">{e(o["person"])}</span></div><div class="chat">{bubbles}{marks}</div>
<div style="display:flex;flex-wrap:wrap;gap:.5rem;border-top:1px solid var(--soft-line);padding-top:.8rem">
<form method="post" action="/console/chat/mark" style="display:flex;gap:.4rem;flex-wrap:wrap">{hidden}
<input class="f" name="note" maxlength="300" placeholder="What went wrong" aria-label="What went wrong" style="width:16rem">
<button class="small" type="submit">Mark a problem</button></form>
<form method="post" action="/console/chat/scenario">{hidden}<button class="small" type="submit">Make it a test scenario</button></form>
<form method="post" action="/console/chat/delete">{hidden}<button class="small" type="submit">Delete conversation</button></form>
</div></section>"""
    else:
        detail = '<section class="card wide"><p class="muted">Choose a conversation.</p></section>' if convs else ""
    lst = (f'<nav class="card side" aria-label="Conversations">{"".join(items)}</nav>' if convs else
           (_not_agreed("their conversations") if not d["anyone"] else
            '<p class="msg">No conversations in the last 7 days.</p>'))
    test = d["test"]
    test_lines = "".join(f'<div class="bubble{" me" if who == "you" else ""}">{e(text)}</div>' for who, text in test["lines"])
    everyone = bool(d["people"]) and all(p["day"] for p in d["people"])
    pretend = "".join(f'<option value="{p["id"]}"{" selected" if p["id"] == test.get("as") else ""}>{e(p["name"])}</option>'
                      for p in d["people"]) if everyone else ""
    test_form = (f"""<form method="post" action="/console/chat/test" class="card tint" id="test">
<label class="f" for="test-msg" style="margin-top:0">Talk to Pepe as a test</label>
<p class="muted">Test mode: the same rules and tools on a throwaway copy of the home's data. It never reaches the device or
their memory{", and it spends a little of the web's daily share" if test["live"] else ""}.</p>
<div class="chat">{test_lines}</div>
<div style="display:flex;gap:.6rem;flex-wrap:wrap"><select class="f" name="as" aria-label="Pretend to be" style="width:10rem">{pretend}</select>
<input class="f" id="test-msg" name="text" maxlength="600" placeholder="Pretend you’re them: “¿Me he tomado la pastilla?”" style="flex:1 1 300px" required>
<button class="small" type="submit">Send</button></div></form>""" if test["available"] and pretend else
                 '<p class="msg">The test chat needs the API key (or the test mode), and everyone at home to have agreed '
                 'you can see their day (it uses a copy of the whole home).</p>')
    return _page("Chat", "/console/chat", d["home"], f"""
<h1>Chat</h1>
<p class="muted" style="max-width:760px">Conversations from the last 7 days, kept only to fix problems, with their OK.
Anything they asked Pepe to forget never appears here.</p>{_notice(note, bad)}
<div class="row">{lst}{detail}</div>
{test_form}""")


# --- 4 · Feedback --------------------------------------------------------------------------------------


def feedback(d: dict, note: str = "", bad: bool = False) -> str:
    filters = [("all", "All")] + [(str(p["id"]), p["name"]) for p in d["people"]] + [("active", "Active"),
                                                                                      ("undone", "Undone")]
    chips = "".join(f'<a class="small{" here" if d["filter"] == k else ""}" href="/console/feedback?f={e(k)}"'
                    f'{" aria-current=true" if d["filter"] == k else ""}>{e(label)}</a>' for k, label in filters)

    def item(f):
        follow = (f'<div class="muted">Follow-up: “{e(f["follow"][0])}” → “{e(f["follow"][1])}”</div>' if f["follow"] else "")
        actions = ""
        if f["status"] in ("active", "replaced"):
            actions += (f'<form method="post" action="/console/feedback/undo"><input type="hidden" name="id" value="{f["id"]}">'
                        f'<button class="small" type="submit">Undo</button></form>'
                        f'<form method="post" action="/console/feedback/undo"><input type="hidden" name="id" value="{f["id"]}">'
                        f'<input type="hidden" name="misunderstood" value="1"><button class="small" type="submit">Misunderstood</button></form>')
        if f["status"] == "active" and f["kind"] == "rule":
            actions += (f'<form method="post" action="/console/feedback/reword" style="display:flex;gap:.4rem">'
                        f'<input type="hidden" name="id" value="{f["id"]}"><input class="f" name="text" maxlength="200" '
                        f'aria-label="New wording" placeholder="New wording" style="width:14rem" required>'
                        f'<button class="small" type="submit">Reword</button></form>')
        status = {"active": ("Active", "ok"), "held_safety": ("Held for you", "wait"), "undone": ("Undone", ""),
                  "replaced": ("Replaced", ""), "not_applied": ("Not applied", ""), "noted": ("Noted", "")}[f["status"]]
        return (f'<article class="card"><div class="kv" style="flex-wrap:wrap"><span>'
                f'{_pill("SAFETY", "safety") + " " if f["safety"] else ""}<b>{e(f["who"])}</b>'
                f'{" (about " + e(f["about"]) + ")" if f["about"] and f["about"] != f["who"] else ""} · '
                f'{e(f["at"].strftime("%-d %b, %H:%M"))}</span>{_pill(status[0], status[1])}</div>'
                f'<p style="font-size:1.1rem;margin:.5rem 0">“{e(f["said"])}”</p>'
                f'<div><b>Understood:</b> {e(f["understood"])}</div><div><b>Changed:</b> {e(f["change"])}</div>{follow}'
                + (f'<div class="muted">Note: {e(f["note"])}</div>' if f["note"] else "")
                + (f'<div style="display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.7rem">{actions}</div>' if actions else "")
                + "</article>")
    needs = [f for f in d["items"] if f["safety"] and f["status"] == "held_safety"]
    rest = [f for f in d["items"] if f not in needs]
    return _page("Feedback", "/console/feedback", d["home"], f"""
<h1>Feedback</h1>
<p class="muted" style="max-width:760px">What they told Pepe about how it behaves, how Pepe understood it, and what changed.
Undo or reword anything; it takes effect on the next turn.</p>{_notice(note, bad)}
{f'<h2>Needs you</h2>{"".join(item(f) for f in needs)}' if needs else ''}
<h2>All feedback</h2><div style="display:flex;gap:.4rem;flex-wrap:wrap">{chips}</div>
{"".join(item(f) for f in rest) or '<p class="muted">Nothing here.</p>'}
<p class="muted">Only the feedback sentence is stored, never the conversation around it. Safety items always reach you;
the rest only for people who agreed you can see their day.</p>""")


# --- 5 · People & memory ---------------------------------------------------------------------------------


def people(d: dict, note: str = "", confirm: dict | None = None) -> str:
    if confirm:
        return _page("Forget", "/console/people", d["home"], f"""
<h1>Forget this for good?</h1><div class="card"><p class="readback">“{e(confirm["text"])}”</p>
<p class="muted">About {e(confirm["person"])}. It is deleted from Pepe’s memory and from the logs, and can't be undone.</p></div>
<form method="post" action="/console/people/forget"><input type="hidden" name="person" value="{confirm["person_id"]}">
<input type="hidden" name="id" value="{confirm["id"]}"><input type="hidden" name="sure" value="1">
<button class="big" type="submit">Yes, forget it</button></form><a class="big second" href="/console/people">No, keep it</a>""")
    cards = []
    for p in d["memory"]:
        if not p["allowed"]:
            cards.append(f'<section class="card"><h2 style="margin-top:0">{e(p["name"])}</h2>'
                         f'<p class="muted">{e(p["name"])} hasn’t agreed that you can see their memory.</p></section>')
            continue
        notes = "".join(f'<li><span class="what">{e(n["text"])}</span><form method="post" action="/console/people/forget">'
                        f'<input type="hidden" name="person" value="{p["id"]}"><input type="hidden" name="id" value="{n["id"]}">'
                        f'<button class="small" type="submit">Forget</button></form></li>' for n in p["notes"])
        people_rows = "".join(f'<li>{e(c["name"])}{", " + e(c["relation"]) if c["relation"] else ""}'
                              f'{" · help contact" if c["help_contact"] else ""}</li>' for c in p["contacts"])
        rules = "".join(f"<li>{e(r)}</li>" for r in p["rules"])
        switches = " · ".join(f'{e(k)}: {"on" if v else "off"}' for k, v in p["switches"].items())
        cards.append(f'<section class="card"><h2 style="margin-top:0">{e(p["name"])}</h2>'
                     f'<div class="label">Interests &amp; life</div><ul class="plain">{notes or "<li class=muted>Nothing yet.</li>"}</ul>'
                     f'<div class="label" style="margin-top:1rem">People in their life</div><ul class="plain">'
                     f'{people_rows or "<li class=muted>None saved.</li>"}</ul>'
                     f'<div class="label" style="margin-top:1rem">Rules from their feedback</div><ul class="plain">'
                     f'{rules or "<li class=muted>None.</li>"}</ul><a href="/console/feedback?f={p["id"]}">See all feedback</a>'
                     f'<p class="muted">{switches}</p></section>')
    shared = d.get("shared")
    shared_html = ""
    if shared:
        contacts = "".join(f'<li>{e(c["name"])}{" (" + e(c["relation"]) + ")" if c["relation"] else ""}'
                           f'{" · Telegram" if c["tg"] else ""}{" · WhatsApp" if c["wa"] else ""}{" · SMS" if c["sms"] else ""}</li>' for c in shared["contacts"])
        shared_html = (f'<section class="card tint"><h2 style="margin-top:0">Shared by both</h2><div class="label">Family contacts</div>'
                       f'<ul class="plain">{contacts or "<li class=muted>None.</li>"}</ul><div class="label" style="margin-top:1rem">'
                       f'Shopping list</div><p>{e(", ".join(shared["shopping"]) or "Empty")}</p>'
                       f'<div class="label">Private between them</div><p>Each one’s health notes and rules stay theirs.</p></section>')
    return _page("People & memory", "/console/people", d["home"], f"""
<h1>People &amp; memory</h1>
<p class="muted" style="max-width:760px">What Pepe knows about each of them. It’s the same list they hear when they ask
“What do you know about me?”, and anything here can be forgotten.</p>{_notice(note)}
<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr))">{"".join(cards)}</div>{shared_html}""")


# --- 6 · Device ----------------------------------------------------------------------------------------


def device(d: dict) -> str:
    dev = d["device"]
    loop = dev["loop"]
    conn_card = ((f'<span class="big-number" style="color:var(--online)">Online</span><span class="muted">heartbeat '
                  f'{loop["ago_min"]} min ago</span>') if loop and loop["online"] else
                 (f'<span class="big-number" style="color:var(--wait)">Offline</span><span class="muted">last seen '
                  f'{loop["ago_min"]} min ago</span>') if loop else
                 '<span class="big-number">Not started</span><span class="muted">Start KORE (Pepe.command on the Mac, or the box) to see it here</span>')
    cards = [f'<div class="card stat"><span class="label">Connection</span>{conn_card}</div>',
             f'<div class="card stat"><span class="label">Clock</span><span class="big-number">'
             f'{"Trusted" if dev["clock_ok"] else "Not trusted"}</span><span class="muted">'
             f'{"Doses fire normally" if dev["clock_ok"] else "Doses are not fired or logged"}</span></div>',
             f'<div class="card stat"><span class="label">Last backup</span><span class="big-number">'
             f'{e(dev["backup"] or "None yet")}</span><span class="muted">Encrypted, off the device</span></div>']
    costs = dev["costs"]
    events = "".join(f'<li><span class="time" style="min-width:8rem">{e(ev["at"])}</span><span class="what">{e(ev["text"])}'
                     f'</span></li>' for ev in d["events"])
    return _page("Device", "/console/device", d["home"], f"""
<h1>Device</h1><p class="muted">The KORE speaker in their living room.</p>
<div class="grid">{"".join(cards)}</div>
<div class="row" style="margin-top:1rem">
<section class="card wide"><h2 style="margin-top:0">Speed &amp; cost</h2>
<div class="kv"><span>Median reply (end of speech to first word)</span><b>Measured from phase 1</b></div>
<div class="kv"><span>Cost today</span><b>${costs["today"]:.2f}</b></div>
<div class="kv"><span>Cost this month</span><b>${costs["month"]:.2f}</b></div>
<p class="muted">Daily limit for all live calls: about 5 euros. The web chat has its own $1 a day inside it.</p></section>
<section class="card side"><h2 style="margin-top:0">Wake word, software</h2>
<p class="muted">Wake-word counts, sensitivity, the self-test, night updates and restarts arrive with the device
(phase 7).</p><div class="kv"><span>Version</span><b>{e(d["version"])}</b></div></section>
</div>
<section class="card"><h2 style="margin-top:0">Recent events</h2>
{f'<ul class="plain">{events}</ul>' if events else '<p class="muted">Nothing yet.</p>'}</section>""",
                 online=bool(loop and loop["online"]))


# --- 7 · Settings ---------------------------------------------------------------------------------------


def settings(d: dict, note: str = "", bad: bool = False) -> str:
    perm = d["permissions"]
    head = "".join(f"<th>{e(n)}</th>" for n in perm["people"])
    rows = "".join(f'<tr><td>{e(label)}</td>' + "".join(
        f'<td>{_pill(v, "ok" if v == "Yes" else "")}</td>' for v in cells) + "</tr>" for label, cells in perm["rows"])
    q = d["quiet"]
    contacts = "".join(f'<li><span class="what"><b>{e(c["name"])}</b>{" (" + e(c["relation"]) + ")" if c["relation"] else ""}'
                       f' · for {e(c["for"])}</span><span class="muted">'
                       f'{" · ".join(x for x in ("Telegram" if c["tg"] else "", "WhatsApp" if c["wa"] else "", "SMS" if c["sms"] else "", "family app" if c["web"] else "") if x) or "not connected"}'
                       f'{" · gets help alerts" if c["help"] else ""}</span></li>' for c in d["contacts"])
    return _page("Settings", "/console/settings", d["home"], f"""
<div style="max-width:880px"><h1>Settings</h1>{_notice(note, bad)}
<section class="card"><h2 style="margin-top:0">Their permissions</h2>
<p class="muted">These are their choices. Only they can change them, by asking Pepe. You can see them here.</p>
<div style="overflow-x:auto"><table class="perm"><thead><tr><th></th>{head}</tr></thead><tbody>{rows}</tbody></table></div></section>
<section class="card"><h2 style="margin-top:0">Quiet times</h2>
<form method="post" action="/console/settings/quiet" class="grid" style="align-items:end">
<div><label class="f" for="ns">Night starts</label><input class="f" type="time" id="ns" name="start" value="{e(q["night_start"])}" required></div>
<div><label class="f" for="ne">Night ends</label><input class="f" type="time" id="ne" name="end" value="{e(q["night_end"])}" required></div>
<button class="big" type="submit" style="margin:0">Save</button></form>
<p class="muted">At night Pepe speaks only if spoken to, or for an emergency. Medication reminders stay, short and soft.
Speed, volume and how Pepe speaks are set by each of them, by voice.</p></section>
<section class="card"><h2 style="margin-top:0">Family contacts</h2>
{f'<ul class="plain">{contacts}</ul>' if contacts else '<p class="muted">None connected yet.</p>'}
<p class="muted">They give family access to the app from their own page (Ajustes → Móviles conectados). Telegram is linked
from the box: <code>uv run python -m kore.family pair</code>.</p></section>
<section class="card"><h2 style="margin-top:0">Data</h2>
<p class="muted">Everything stays on the box at home, with a nightly encrypted backup. Conversations on their page are
deleted after 90 days (you see only the last 7). Memory and the feedback log stay until someone asks Pepe to forget
them. A copy of their data, or deleting everything, is done on their request from the box’s command line.</p></section>
</div>""")
