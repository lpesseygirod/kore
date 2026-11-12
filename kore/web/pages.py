"""The HTML of the home web page: plain, big, Spanish, no scripts, nothing loaded from outside.

Every value from the database goes through `e()` (escaped), so a name or a list item can't
become HTML.
"""

from __future__ import annotations

import contextvars
from html import escape

from kore.web import icons

CSS = """
/* The look from Lea's design (docs/design/Style.dc.html): wine, linen, Inter, big and calm. */
:root { --bg: #FBF7F5; --fg: #2A1A1F; --muted: #6F5A61; --card: #FFFFFF; --line: #E6D7DC; --soft-line: #EFE4E8;
        --accent: #6E1E35; --accent-dark: #4A1224; --tint: #F3E6EA; --tint-line: #E2C9D1; --on-accent: #FFFFFF;
        --ok-bg: #E4F0EA; --ok: #245640; --wait-bg: #FBEFDF; --wait: #7A410C; --off-bg: #EFEAEC; --off: #4F464A;
        --online: #2F6B4F; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #1C1517; --fg: #F6EDF0; --muted: #CDB9C0; --card: #261D20; --line: #44343A; --soft-line: #3A2C31;
          --accent: #E7A3B6; --accent-dark: #F3C6D3; --tint: #3A2530; --tint-line: #5A3A47; --on-accent: #1C1517;
          --ok-bg: #1F3A2D; --ok: #9AD5B4; --wait-bg: #3D2B14; --wait: #F2C27A; --off-bg: #33292D; --off: #D8CCD0;
          --online: #8FD3AE; }
}
* { box-sizing: border-box; }
html { font-size: 20px; }
html.compact { font-size: 17px; }
body { margin: 0; background: var(--bg); color: var(--fg); line-height: 1.5;
       font-family: Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 40rem; margin: 0 auto; padding: 1.4rem 16px 3rem; }
html.compact main { max-width: 1200px; padding: 2rem 24px 4rem; }
h1 { font-size: 1.8rem; line-height: 1.22; margin: .3rem 0 1rem; font-weight: 700; }
h2 { font-size: 1.15rem; line-height: 1.4; margin: 1.6rem 0 .6rem; font-weight: 700; }
p, li { font-size: 1rem; }
.muted { color: var(--muted); }
.label { font-size: .75rem; font-weight: 600; letter-spacing: .06em; color: var(--muted); text-transform: uppercase; }
a { color: var(--accent); }
a:hover { color: var(--accent-dark); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 1rem 1.2rem;
        margin: .7rem 0; }
.card.tint { background: var(--tint); border-color: var(--tint-line); }
.card.hero { background: var(--accent); border-color: var(--accent); color: var(--on-accent); }
.card.hero .label, .card.hero .muted { color: var(--tint); }
ul.plain { list-style: none; padding: 0; margin: 0; }
ul.plain li { padding: .6rem 0; border-bottom: 1px solid var(--soft-line); display: flex; gap: .9rem;
              align-items: center; flex-wrap: wrap; }
ul.plain li:last-child { border-bottom: 0; }
.time { font-weight: 600; min-width: 3.2rem; font-variant-numeric: tabular-nums; }
.tag { margin-left: auto; font-size: .8rem; color: var(--muted); white-space: nowrap; }
.pill { display: inline-block; padding: .2rem .65rem; border-radius: 999px; font-size: .75rem; font-weight: 600;
        background: var(--off-bg); color: var(--off); white-space: nowrap; }
.pill.ok, .tag.ok { background: var(--ok-bg); color: var(--ok); }
.pill.wait, .tag.warn { background: var(--wait-bg); color: var(--wait); }
.tag.ok, .tag.warn { padding: .2rem .65rem; border-radius: 999px; font-weight: 600; }
.pill.safety { background: var(--accent); color: var(--on-accent); letter-spacing: .04em; font-weight: 700; }
header.top { background: var(--card); border-bottom: 1px solid var(--line); }
header.top .bar { max-width: 40rem; margin: 0 auto; padding: .6rem 16px; display: flex; flex-wrap: wrap;
                  align-items: center; gap: .4rem 1rem; }
html.compact header.top .bar { max-width: 1200px; padding: .8rem 24px; gap: .8rem 2rem; }
.mark { font-size: 1.1rem; font-weight: 700; letter-spacing: .18em; color: var(--accent); text-decoration: none; }
nav { display: flex; gap: .25rem; flex-wrap: wrap; flex: 1 1 auto; align-items: center; }
nav a, nav button { font: inherit; font-size: .8rem; color: var(--muted); text-decoration: none; background: none;
      border: 0; padding: .55rem .65rem; border-radius: 8px; cursor: pointer; min-height: 44px; display: inline-flex;
      align-items: center; }
nav a.here { background: var(--tint); color: var(--accent); font-weight: 600; }
nav form { margin-left: auto; }
.home-chip { display: flex; align-items: center; gap: .4rem; padding: .35rem .75rem; border: 1px solid var(--line);
             border-radius: 999px; font-size: .75rem; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--online); display: inline-block; }
.dot.off { background: var(--wait); }
button.big, a.big { display: block; width: 100%; font: inherit; font-size: 1.05rem; font-weight: 600;
      text-align: center; padding: .85rem 1rem; margin: .6rem 0; border-radius: 12px; min-height: 48px;
      border: 1px solid var(--accent); background: var(--accent); color: var(--on-accent); cursor: pointer;
      text-decoration: none; }
a.big:hover, button.big:hover { background: var(--accent-dark); color: var(--on-accent); }
button.big.second, a.big.second { background: var(--card); color: var(--accent); }
a.small.here { background: var(--tint); border-color: var(--tint-line); }
button.small, a.small { text-decoration: none; display: inline-flex; align-items: center; font: inherit;
      font-size: .8rem; font-weight: 600; padding: .35rem .8rem; min-height: 44px; border-radius: 10px;
      border: 1px solid var(--line); background: var(--card); color: var(--accent); cursor: pointer; }
input.code { font: inherit; font-size: 2rem; letter-spacing: .4em; text-align: center; width: 100%;
      padding: .6rem; border-radius: 12px; border: 1px solid var(--line); background: var(--card); color: var(--fg); }
.chat { display: flex; flex-direction: column; gap: .6rem; margin: 1rem 0; }
.bubble { max-width: 85%; padding: .65rem .9rem; border-radius: 16px 16px 16px 4px; border: 1px solid var(--line);
          background: var(--card); white-space: pre-wrap; overflow-wrap: anywhere; }
.bubble.me { align-self: flex-end; background: var(--accent); border-color: var(--accent); color: var(--on-accent);
             border-radius: 16px 16px 4px 16px; }
.bubble.me .when { color: var(--tint); }
.bubble img { display: block; max-width: 100%; height: auto; border-radius: 10px; margin-bottom: .3rem; }
.bubble .when { display: block; font-size: .7rem; color: var(--muted); margin-top: .25rem; }
.note-line { align-self: center; padding: .3rem .8rem; border-radius: 999px; background: var(--off-bg);
             color: var(--off); font-size: .7rem; }
textarea.say { font: inherit; width: 100%; min-height: 5.5rem; padding: .7rem; border-radius: 12px;
               border: 1px solid var(--line); background: var(--card); color: var(--fg); }
.msg { padding: .8rem 1rem; border-radius: 14px; border: 1px solid var(--line); background: var(--card); }
.msg.bad { border-color: var(--wait); background: var(--wait-bg); color: var(--wait); }
.msg.good { border-color: var(--ok); background: var(--ok-bg); color: var(--ok); }
label.f { display: block; margin: .9rem 0 .3rem; font-weight: 600; }
input.f, select.f { font: inherit; width: 100%; min-height: 48px; padding: .55rem .75rem; border-radius: 10px;
      border: 1px solid var(--line); background: var(--card); color: var(--fg); }
.check { display: flex; gap: .55rem; align-items: center; margin: .6rem 0; font-size: 1rem; min-height: 44px; }
.check input { width: 1.4rem; height: 1.4rem; accent-color: var(--accent); }
.days { display: flex; flex-wrap: wrap; gap: .1rem 1rem; }
.readback { font-size: 1.2rem; font-weight: 600; }
ul.plain li form { margin-left: auto; }
ul.plain li .what { flex: 1 1 12rem; min-width: 0; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }
.stat { display: flex; flex-direction: column; gap: .3rem; text-decoration: none; color: var(--fg); margin: 0; }
.stat .big-number { font-size: 1.3rem; font-weight: 700; }
.row { display: flex; flex-wrap: wrap; gap: 24px; align-items: flex-start; }
.row > .wide { flex: 999 1 520px; min-width: 0; }
.row > .side { flex: 1 1 300px; min-width: 0; }
.kv { display: flex; justify-content: space-between; gap: 1rem; padding: .35rem 0; }
.week { overflow-x: auto; border: 1px solid var(--line); border-radius: 16px; background: var(--card); }
.week .days7 { min-width: 980px; display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); }
.week .day { border-right: 1px solid var(--soft-line); padding: .7rem .5rem; display: flex; flex-direction: column;
             gap: .4rem; min-height: 300px; }
.week .day:last-child { border-right: 0; }
.week .day.today { background: var(--tint); }
.ev { border-radius: 8px; padding: .4rem; font-size: .7rem; line-height: 1.35; background: var(--off-bg); }
.ev.med { background: var(--tint); } .ev.appt { background: var(--wait-bg); }
.ev.bday { background: var(--accent); color: var(--on-accent); }
.legend { display: flex; flex-wrap: wrap; gap: .4rem 1.2rem; font-size: .75rem; color: var(--muted); }
.sw { width: 14px; height: 14px; border-radius: 4px; display: inline-block; vertical-align: middle;
      margin-right: .4rem; border: 1px solid var(--line); }
table.perm { width: 100%; border-collapse: collapse; font-size: .85rem; }
table.perm th, table.perm td { text-align: left; padding: .55rem .4rem; border-bottom: 1px solid var(--soft-line); }
html.size-mayor { font-size: 23px; } html.size-enorme { font-size: 26px; }
body:has(nav.tabs) main { padding-bottom: 7rem; }
nav.tabs { position: fixed; left: 0; right: 0; bottom: 0; z-index: 5; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
           background: var(--card); border-top: 1px solid var(--line); padding: .3rem .3rem 1rem; gap: 0; }
nav.tabs a { flex-direction: column; justify-content: center; gap: .15rem; font-size: .75rem; min-height: 64px;
             font-weight: 600; color: var(--off); }
nav.tabs a.here { background: none; color: var(--accent); font-weight: 700; }
nav.tabs svg { width: 1.4rem; height: 1.4rem; }
.big-card { display: flex; justify-content: space-between; align-items: center; gap: .8rem; background: var(--card);
            border: 2px solid var(--line); border-radius: 20px; padding: .8rem 1rem; color: var(--fg);
            text-decoration: none; margin: .7rem 0; }
.big-card b { font-size: 1.05rem; } .big-card .chev { font-size: 1.6rem; color: var(--accent); }
.next { background: var(--accent); color: var(--on-accent); border-radius: 20px; padding: 1rem; margin: .7rem 0;
        display: flex; flex-direction: column; gap: .6rem; }
.next .label { color: var(--tint); } .next .what { font-size: 1.15rem; font-weight: 600; }
.next a { background: var(--card); color: var(--accent); border-radius: 14px; min-height: 56px; display: flex;
          align-items: center; justify-content: center; font-weight: 700; text-decoration: none; }
a.talk { min-height: 80px; border-radius: 20px; background: var(--tint); border: 2px solid var(--tint-line);
         display: flex; align-items: center; justify-content: center; gap: .6rem; color: var(--accent);
         font-size: 1.1rem; font-weight: 700; text-decoration: none; margin: .7rem 0; }
.day-tabs { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .4rem; margin: .5rem 0 1rem; }
.day-tabs a { min-height: 64px; border-radius: 14px; border: 2px solid var(--tint-line); background: var(--card);
              color: var(--fg); display: flex; flex-direction: column; align-items: center; justify-content: center;
              text-decoration: none; font-weight: 600; font-size: .85rem; line-height: 1.2; }
.day-tabs a.here { background: var(--accent); border-color: var(--accent); color: var(--on-accent); font-weight: 700; }
.item { display: flex; gap: .7rem; align-items: center; background: var(--card); border: 2px solid var(--line);
        border-radius: 16px; padding: .7rem .8rem; margin: .5rem 0; }
.item b { min-width: 3.2rem; } .item.done { opacity: .75; }
.item.bday { background: var(--accent); border-color: var(--accent); color: var(--on-accent); }
.item.appt { background: var(--wait-bg); border-color: var(--wait-bg); }
.item.med { background: var(--tint); border-color: var(--tint); } .item.med.done { background: var(--card); }
.item .sub { display: block; font-size: .8rem; color: var(--muted); }
.ok-text { color: var(--ok); font-weight: 600; }
.pair { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .5rem; }
.pair a, .pair button { margin: 0; }
button.huge { min-height: 96px; font-size: 1.2rem; border-radius: 22px; }
.skip { position: absolute; left: -999px; } .skip:focus { left: 8px; top: 8px; background: var(--card); padding: .5rem; }
"""


# Per request (set by the app for a person's login): their text size and tú/usted.
SIZE: contextvars.ContextVar[str] = contextvars.ContextVar("size", default="grande")
TU: contextvars.ContextVar[bool] = contextvars.ContextVar("tu", default=False)


def t(tu_text: str, usted_text: str) -> str:
    return tu_text if TU.get() else usted_text


def e(value) -> str:
    return escape("" if value is None else str(value), quote=True)


def layout(title: str, body: str, nav: str = "", lang: str = "es", compact: bool = False, after: str = "") -> str:
    """compact: Lea's console (a wider page, smaller type, in English). `after`: what goes below the page (the
    family app's tab bar)."""
    skip = "Skip to content" if lang == "en" else "Ir al contenido"
    classes = " ".join(c for c in ("compact" if compact else "", f"size-{SIZE.get()}" if not compact else "") if c)
    return (f'<!doctype html><html lang="{lang}" class="{classes}"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="color-scheme" content="light dark"><title>{e(title)}</title>{icons.HEAD}'
            f'<style>{CSS}</style></head><body><a class="skip" href="#main">{skip}</a>{nav}'
            f'<main id="main">{body}</main>{after}</body></html>')


def menu(here: str, account) -> str:
    if account.role == "person":
        return (f'<header class="top"><div class="bar"><a class="mark" href="/">KORE</a><nav aria-label="Arriba">'
                f'<a href="/ajustes" class="{"here" if here == "/ajustes" else ""}" style="margin-left:auto">Ajustes</a>'
                f'</nav></div></header>' + tabs(here))
    mine = [("/hablar", "Hablar"), ("/calendario", "Calendario"), ("/compra", "La compra"), ("/sabe", "Sobre mí")]
    if account.role == "family":
        return (f'<header class="top"><div class="bar"><a class="mark" href="/">KORE</a><nav aria-label="Arriba">'
                f'<a href="/dispositivos" style="margin-left:auto">Dispositivos</a><form method="post" action="/salir">'
                f'<button type="submit">Salir</button></form></nav></div></header>'
                + tabs(here, [("/", "Inicio", "home"), ("/familia/calendario", "Calendario", "cal"),
                              ("/familia/mensajes", "Mensajes", "chat"), ("/familia/avisos", "Avisos", "bell")]))
    else:
        items = [("/", "Hoy")] + (mine if account.role == "person" else []) + [("/dispositivos", "Dispositivos")]
    links = "".join(f'<a href="{href}" class="{"here" if href == here else ""}"'
                    f'{" aria-current=page" if href == here else ""}>{e(label)}</a>' for href, label in items)
    return (f'<header class="top"><div class="bar"><a class="mark" href="/">KORE</a><nav aria-label="Menú">{links}'
            f'<form method="post" action="/salir"><button type="submit">Salir</button></form></nav></div></header>')


ICONS = {
    "home": '<path d="M3 11 L12 4 L21 11"/><path d="M5 10 V20 H19 V10"/>',
    "chat": '<path d="M4 5 H20 V16 H9 L4 20 Z"/>',
    "cal": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10 H21 M8 3 V7 M16 3 V7"/>',
    "family": '<circle cx="9" cy="8" r="3"/><circle cx="17" cy="9" r="2.5"/>'
              '<path d="M3 20 A6 6 0 0 1 15 20 M14 20 A4.5 4.5 0 0 1 21 17"/>',
    "bell": '<path d="M6 16 V11 A6 6 0 0 1 18 11 V16 L20 18 H4 Z"/><path d="M10 21 H14"/>',
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11 A7 7 0 0 0 19 11 M12 18 V21"/>',
}
TAB_OF = {"/familia/cita": "/familia/calendario", "/": "/", "/hablar": "/hablar", "/agenda": "/agenda", "/calendario": "/agenda", "/pastillas": "/agenda",
          "/compra": "/agenda", "/mi-familia": "/mi-familia", "/fotos": "/mi-familia"}


def icon(name: str, size: int = 28) -> str:
    return (f'<svg aria-hidden="true" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
            f'{ICONS[name]}</svg>')


def tabs(here: str, items=None) -> str:
    items = items or [("/", "Inicio", "home"), ("/hablar", "Pepe", "chat"), ("/agenda", "Agenda", "cal"),
                      ("/mi-familia", "Familia", "family")]
    current = TAB_OF.get(here, here)
    links = "".join(f'<a href="{href}" class="{"here" if href == current else ""}"'
                    f'{" aria-current=page" if href == current else ""}>{icon(ic)}{e(label)}</a>'
                    for href, label, ic in items)
    return f'<nav class="tabs" aria-label="Menú">{links}</nav>'


# --- logging in ------------------------------------------------------------------------------


def who_are_you(people: list[dict], message: str = "", bad: bool = False) -> str:
    buttons = "".join(
        f'<form method="post" action="/entrar/codigo"><input type="hidden" name="person" value="{p["id"]}">'
        f'<button class="big" type="submit">Soy {e(p["call_name"])}</button></form>' for p in people)
    note = f'<p class="msg{" bad" if bad else ""}" role="status">{e(message)}</p>' if message else ""
    return layout("Pepe — Entrar", f"""
<h1>Hola. ¿Quién es usted?</h1>
{note}
<p class="muted">Pepe dirá en voz alta un código de seis números. Así sabemos que está en casa.</p>
{buttons or '<p class="msg">Todavía no hay nadie en casa apuntado en Pepe.</p>'}
<p class="muted" style="margin-top:2rem">¿Tiene ya un código? <a href="/entrar/codigo">Escríbalo aquí</a>.</p>
""")


def enter_code(message: str = "", bad: bool = False) -> str:
    note = f'<p class="msg{" bad" if bad else ""}" role="status">{e(message)}</p>' if message else ""
    return layout("Pepe — Código", f"""
<h1>Escriba el código</h1>
{note}
<form method="post" action="/entrar">
  <label for="code" class="muted">Los seis números que ha dicho Pepe</label>
  <input class="code" id="code" name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="12"
         pattern="[0-9 ]*" required autofocus>
  <button class="big" type="submit">Entrar</button>
</form>
<p class="muted"><a href="/entrar">Volver</a></p>
""")


# --- the day ---------------------------------------------------------------------------------


def today(account, day: dict) -> str:
    parts = [f"<h1>Hola, {e(account.name)}</h1><p class='muted'>Hoy es {e(day['date'])}.</p>"]
    if not day.get("clock_ok", True):
        parts.append('<p class="msg bad">La hora de Pepe no es fiable ahora mismo, así que las medicinas no se '
                     "muestran. Pepe lo dice también en voz alta.</p>")
    w = day.get("weather")
    if w:
        rest = w.get("rest_of_today") or {}
        sky = rest.get("sky") or w.get("sky")
        line = f"{e(sky).capitalize()}, entre {e(w.get('min_c'))} y {e(w.get('max_c'))} grados."
        if w.get("forecast_from"):
            line += " <span class='muted'>(previsión de antes: ahora no hay conexión)</span>"
        parts.append(f'<h2>El tiempo</h2><div class="card">{line}</div>')
    meds = day.get("medicines")
    if meds:
        rows = "".join(
            f'<li><span class="time">{e(m["time"])}</span><span>{e(m["name"])}</span>'
            f'<span class="tag {"ok" if m["state"] == "tomada" else "warn" if m["state"] == "sin apuntar" else ""}">'
            f'{e(m["state"])}{" a las " + e(m["taken_at"]) if m.get("taken_at") else ""}</span></li>' for m in meds)
        parts.append(f'<h2>Medicinas de hoy</h2><div class="card"><ul class="plain">{rows}</ul></div>'
                     '<p class="muted">«Sin apuntar» quiere decir que Pepe no lo sabe, no que no se la haya '
                     "tomado. Si tiene dudas, mire el pastillero.</p>")
    sections = [("Citas", day.get("appointments"), lambda a: (a["time"], a["title"] + (f" — {a['where']}" if a["where"]
                                                                                      else ""))),
                ("Recordatorios", day.get("reminders"), lambda r: (r["time"], r["text"])),
                ("Cumpleaños", day.get("birthdays"), lambda b: ("hoy" if b["in_days"] == 0 else
                                                               "mañana" if b["in_days"] == 1 else f"en {b['in_days']} días",
                                                               b["name"] + (f" ({b['relation']})" if b["relation"] else "")))]
    for title, items, fmt in sections:
        if items:
            rows = "".join(f'<li><span class="time">{e(fmt(i)[0])}</span><span>{e(fmt(i)[1])}</span></li>' for i in items)
            parts.append(f'<h2>{title}</h2><div class="card"><ul class="plain">{rows}</ul></div>')
    if day.get("family_proposals"):
        names = sorted({p["from"] for p in day["family_proposals"]})
        parts.append(f'<p class="msg">{e(" y ".join(names))} le '
                     f'{"ha" if len(day["family_proposals"]) == 1 else "han"} propuesto '
                     f'{"una cita" if len(day["family_proposals"]) == 1 else "citas"}. '
                     '<a href="/calendario#propuestas">Mírelas en el calendario</a>.</p>')
    shop = day.get("shopping") or []
    if shop:
        parts.append(f'<h2>La compra</h2><div class="card"><ul class="plain">'
                     + "".join(f"<li>{e(i)}</li>" for i in shop) + "</ul></div>")
    if len(parts) == 1:
        parts.append('<p class="msg">Hoy no hay nada apuntado.</p>')
    return layout("Pepe — Hoy", "".join(parts), menu("/", account))


def operator_home(account, status: dict) -> str:
    rows = "".join(f'<li><span>{e(k)}</span><span class="tag">{e(v)}</span></li>' for k, v in status.items())
    return layout("Pepe — Estado", f"<h1>Estado de Pepe</h1><div class='card'><ul class='plain'>{rows}</ul></div>"
                  "<p class='muted'>Solo el estado: las conversaciones y las notas son de cada persona.</p>",
                  menu("/", account))


def devices(account, rows: list[dict], family: list[dict] | None = None) -> str:
    items = "".join(
        f'<li><span>{e(r["device"])}{" — " + e(r["who"]) if account.role == "operator" else ""}'
        f'<br><span class="muted">desde el {e(r["since"].strftime("%d/%m/%Y"))}, usado el '
        f'{e(r["seen"].strftime("%d/%m/%Y"))}</span></span>'
        + ('<span class="tag">este</span>' if r["this_one"] else
           f'<form method="post" action="/dispositivos/cerrar" class="tag"><input type="hidden" name="login" '
           f'value="{r["id"]}"><button class="small" type="submit">Cerrar</button></form>') + "</li>" for r in rows)
    return layout("Pepe — Dispositivos",
                  f"<h1>Dispositivos</h1><p class='muted'>Los móviles y tabletas que pueden entrar"
                  f"{' en la página de esta casa' if account.role == 'operator' else ' con su nombre'}. Si alguno no es "
                  f"suyo, ciérrelo.</p><div class='card'><ul class='plain'>{items}</ul></div>"
                  + (_family_access(family) if family is not None else ""),
                  menu("/dispositivos", account))


def message(title: str, text: str, back: str = "/") -> str:
    return layout(f"Pepe — {title}",
                  f"<h1>{e(title)}</h1><p class='msg'>{e(text)}</p><p><a href='{e(back)}'>Volver</a></p>")


# --- talking -----------------------------------------------------------------------------------


def chat(account, rows: list[dict], available: bool, note: str = "") -> str:
    bubbles = "".join(
        f'<div class="bubble{" me" if r["who"] == "person" else ""}">'
        + (f'<img src="/foto?id={int(r["photo_id"])}" alt="Foto que mandó a Pepe">' if r.get("photo_id") else "")
        + f'{e(r["text"])}'
        f'<span class="when">{"Usted" if r["who"] == "person" else "Pepe"}, {e(r["at"].strftime("%d/%m %H:%M"))}'
        f'{" (en voz alta)" if r["channel"] == "voice" else ""}</span></div>' for r in rows)
    form = (f'<form method="post" action="/hablar" id="escribir" enctype="multipart/form-data">'
            f'<label for="t" class="muted">Escriba a Pepe</label>'
            f'<textarea class="say" id="t" name="text" maxlength="600" autofocus></textarea>'
            f'<label class="f" for="photo">Una foto (si quiere)</label>'
            f'<input class="f" type="file" id="photo" name="photo" accept="image/jpeg,image/png,image/webp,image/gif">'
            f'<button class="big" type="submit">Enviar</button></form>'
            '<p class="muted">Pepe tarda unos segundos en contestar. Las fotos se guardan solo en Pepe, sin el lugar '
            'donde se hicieron, y solo las ve usted.</p>') if available else (
            '<p class="msg">Ahora no se puede escribir a Pepe desde aquí. Puede hablarle en voz alta.</p>')
    notice = f'<p class="msg bad" role="status">{e(note)}</p>' if note else ""
    empty = '<p class="muted">Todavía no hay nada escrito. Puede preguntarle lo mismo que en voz alta.</p>'
    return layout("Pepe — Hablar", f"""
<h1 style="margin-bottom:.2rem">Pepe</h1>
<p class="muted" style="margin-top:0">El mismo Pepe de casa. Se acuerda de lo que {t("le cuentas", "le cuenta")}.</p>
{notice}
<div class="chat" id="ultimo">{bubbles or empty}</div>
{form}
<p class="muted" style="margin-top:2rem">Lo escrito aquí se guarda 90 días. Lo hablado en voz alta solo aparece si usted
lo ha permitido. <a href="/hablar/borrar">Borrar la conversación</a></p>
""", menu("/hablar", account))


def confirm_delete(account) -> str:
    return layout("Pepe — Borrar", """
<h1>¿Borrar la conversación?</h1>
<p>Se borra todo lo escrito y lo hablado que aparece en «Hablar». Lo que Pepe tiene apuntado (medicinas, citas,
la compra, lo que sabe de usted) no se borra.</p>
<form method="post" action="/hablar/borrar"><button class="big" type="submit">Sí, borrar</button></form>
<a class="big second" href="/hablar">No, volver</a>
""", menu("/hablar", account))


# --- calendar, shopping list, what Pepe knows (W3) ----------------------------------------------

DONE = {"apuntado": "Hecho. Pepe lo recordará en voz alta cuando toque.", "quitado": "Hecho, quitado.",
        "olvidado": "Hecho. Pepe lo ha olvidado.", "anulado": "No se ha cambiado nada.",
        "enviado": "Mensaje enviado. Pepe les dirá que tienen un mensaje suyo.",
        "propuesta": "Propuesta enviada. La verán en su página y decidirán si la apuntan.",
        "mandado": "Enviado.", "foto": "Foto enviada. La verán en su página.",
        "no_enviada": "No se ha podido enviar la foto: esa persona ya no está conectada. No se ha guardado.",
        "no_enviado": "No se ha podido enviar: esa persona ya no está conectada.", "pendiente": "Todavía no se ha podido enviar. Pepe lo sigue intentando y "
        "avisará cuando salga."}


def _notice(note: str, bad: bool = False, good: bool = False) -> str:
    if not note:
        return ""
    return f'<p class="msg{" bad" if bad else " good" if good else ""}" role="status">{e(note)}</p>'


def _who_tag(shared: bool) -> str:
    return '<span class="tag">los dos</span>' if shared else ""


def _post_button(action: str, field: str, value, label: str) -> str:
    return (f'<form method="post" action="{action}"><input type="hidden" name="{field}" value="{e(value)}">'
            f'<button class="small" type="submit">{e(label)}</button></form>')


def calendar(account, cal: dict, note: str = "", bad: bool = False) -> str:
    parts = [f"<h1>Calendario</h1>{_notice(note, bad, good=not bad)}"]
    if not cal["clock_ok"]:
        parts.append(_notice("La hora de Pepe no es fiable ahora mismo: se puede mirar, pero no cambiar.", bad=True))
    props = "".join(
        f'<li><span class="what"><b>{e(p["title"])}</b><br><span class="muted">{e(p["when"])}'
        f'{" — " + e(p["where"]) if p["where"] else ""}. La propone {e(p["from"])}.</span></span>'
        f'{_who_tag(p["shared"])}{_post_button("/calendario/propuesta", "id", p["id"], "Apuntarla")}'
        f'{_post_button("/calendario/propuesta/no", "id", p["id"], "No")}</li>' for p in cal.get("proposals", []))
    if props:
        parts.append(f'<h2 id="propuestas">Propuestas de la familia</h2><div class="card"><ul class="plain">{props}'
                     '</ul></div><p class="muted">No se apuntan hasta que usted lo confirme.</p>')
    appts = "".join(
        f'<li><span class="what"><b>{e(a["title"])}</b><br><span class="muted">{e(a["when"])}'
        f'{" — " + e(a["where"]) if a["where"] else ""}</span></span>{_who_tag(a["shared"])}'
        f'<a class="small" href="/calendario/cita?id={a["id"]}">Cambiar</a></li>' for a in cal["appointments"])
    parts.append("<h2>Citas</h2>" + (f'<div class="card"><ul class="plain">{appts}</ul></div>' if appts else
                                      '<p class="muted">No hay citas apuntadas.</p>')
                 + '<a class="big" href="/calendario/nueva-cita">Apuntar una cita</a>')
    rems = "".join(
        f'<li><span class="what">{e(r["text"])}<br><span class="muted">{e(r["when"])}</span></span>'
        f'{_who_tag(r["shared"])}{_post_button("/calendario/recordatorio/quitar", "id", r["id"], "Quitar")}</li>'
        for r in cal["reminders"])
    parts.append("<h2>Recordatorios</h2>" + (f'<div class="card"><ul class="plain">{rems}</ul></div>' if rems else
                                              '<p class="muted">No hay recordatorios.</p>')
                 + '<a class="big" href="/calendario/nuevo-recordatorio">Apuntar un recordatorio</a>')
    bdays = "".join(
        f'<li><span class="what">{e(b["name"])}{" (" + e(b["relation"]) + ")" if b["relation"] else ""}'
        f'<br><span class="muted">{e(b["when"])}</span></span>{_who_tag(b["shared"])}'
        f'{_post_button("/calendario/cumple/quitar", "id", b["id"], "Quitar")}</li>' for b in cal["birthdays"])
    parts.append("<h2>Cumpleaños</h2>" + (f'<div class="card"><ul class="plain">{bdays}</ul></div>' if bdays else
                                           '<p class="muted">No hay cumpleaños apuntados.</p>')
                 + '<a class="big" href="/calendario/nuevo-cumple">Apuntar un cumpleaños</a>')
    parts.append('<p class="muted">Lo que se apunta aquí, Pepe lo recuerda en voz alta, y lo que se le dice a Pepe '
                 "aparece aquí. Las medicinas solo se cambian hablando con Pepe.</p>")
    return layout("Pepe — Calendario", "".join(parts), menu("/calendario", account))


def _shared_box(checked: bool = False) -> str:
    return (f'<label class="check"><input type="checkbox" name="shared" value="1"{" checked" if checked else ""}> '
            "Para los dos (también para la otra persona de casa)</label>")


def _date_time(f: dict, today: str) -> str:
    return (f'<label class="f" for="day">Día</label><input class="f" type="date" id="day" name="day" min="{e(today)}" '
            f'value="{e(f.get("day", ""))}" required>'
            f'<label class="f" for="time">Hora</label><input class="f" type="time" id="time" name="time" '
            f'value="{e(f.get("time", ""))}" required>')


def new_appointment(account, today: str, f: dict | None = None, note: str = "") -> str:
    f = f or {}
    return layout("Pepe — Nueva cita", f"""
<h1>Apuntar una cita</h1>{_notice(note, bad=True)}
<form method="post" action="/calendario/nueva-cita">
<label class="f" for="title">¿Qué es?</label>
<input class="f" id="title" name="title" maxlength="120" placeholder="Médico de cabecera" value="{e(f.get('title', ''))}"
 required>
{_date_time(f, today)}
<label class="f" for="where">¿Dónde? (si quiere)</label>
<input class="f" id="where" name="where" maxlength="120" value="{e(f.get('where', ''))}">
{_shared_box(bool(f.get('shared')))}
<button class="big" type="submit">Seguir</button>
</form>
<p class="muted">Antes de guardarla, verá cómo queda para confirmarla.</p>
<a href="/calendario">Volver</a>""", menu("/calendario", account))


def change_appointment(account, a: dict, today: str, note: str = "") -> str:
    return layout("Pepe — Cambiar cita", f"""
<h1>{e(a['title'])}</h1>{_notice(note, bad=True)}
<p>Ahora: {e(a['when'])}{' — ' + e(a['where']) if a['where'] else ''}.</p>
<h2>Cambiar el día o la hora</h2>
<form method="post" action="/calendario/cita"><input type="hidden" name="id" value="{a['id']}">
{_date_time({'day': a['day'], 'time': a['time']}, today)}
<button class="big" type="submit">Seguir</button></form>
<h2>Anular la cita</h2>
<form method="post" action="/calendario/cita/anular"><input type="hidden" name="id" value="{a['id']}">
<button class="big" type="submit">Anular esta cita</button></form>
<a href="/calendario">Volver</a>""", menu("/calendario", account))


def new_reminder(account, today: str, f: dict | None = None, note: str = "") -> str:
    from kore.brain.fallback import WEEKDAYS
    f = f or {}
    how = f.get("how", "")
    codes = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
    days = "".join(f'<label class="check"><input type="checkbox" name="d_{c}" value="1"'
                   f'{" checked" if f.get("d_" + c) else ""}> {e(WEEKDAYS[i])}</label>' for i, c in enumerate(codes))

    def radio(value, label):
        return (f'<label class="check"><input type="radio" name="how" value="{value}"'
                f'{" checked" if how == value else ""} required> {label}</label>')
    return layout("Pepe — Nuevo recordatorio", f"""
<h1>Apuntar un recordatorio</h1>{_notice(note, bad=True)}
<form method="post" action="/calendario/nuevo-recordatorio">
<label class="f" for="text">¿Qué le recuerda Pepe?</label>
<input class="f" id="text" name="text" maxlength="120" placeholder="Sacar la basura" value="{e(f.get('text', ''))}" required>
<label class="f" for="time">¿A qué hora?</label>
<input class="f" type="time" id="time" name="time" value="{e(f.get('time', ''))}" required>
<p class="f"><b>¿Cuándo?</b></p>
{radio('once', 'Un solo día:')}
<input class="f" type="date" name="day" min="{e(today)}" value="{e(f.get('day', ''))}" aria-label="Día">
{radio('daily', 'Todos los días')}
{radio('weekly', 'Algunos días de la semana:')}
<div class="days">{days}</div>
{_shared_box(bool(f.get('shared')))}
<button class="big" type="submit">Seguir</button>
</form>
<a href="/calendario">Volver</a>""", menu("/calendario", account))


def new_birthday(account, f: dict | None = None, note: str = "") -> str:
    from kore.brain.fallback import MONTHS
    f = f or {}
    months = "".join(f'<option value="{i}"{" selected" if f.get("month") == str(i) else ""}>{e(m)}</option>'
                     for i, m in enumerate(MONTHS, 1))
    return layout("Pepe — Nuevo cumpleaños", f"""
<h1>Apuntar un cumpleaños</h1>{_notice(note, bad=True)}
<form method="post" action="/calendario/nuevo-cumple">
<label class="f" for="name">¿De quién?</label>
<input class="f" id="name" name="name" maxlength="60" value="{e(f.get('name', ''))}" required>
<label class="f" for="relation">¿Quién es para usted? (si quiere)</label>
<input class="f" id="relation" name="relation" maxlength="40" placeholder="nieta" value="{e(f.get('relation', ''))}">
<label class="f" for="bday">Día</label>
<input class="f" type="number" id="bday" name="day" min="1" max="31" inputmode="numeric" value="{e(f.get('day', ''))}"
 required>
<label class="f" for="month">Mes</label>
<select class="f" id="month" name="month" required><option value="">—</option>{months}</select>
{_shared_box(bool(f.get('shared')))}
<button class="big" type="submit">Seguir</button>
</form>
<a href="/calendario">Volver</a>""", menu("/calendario", account))


def confirm(account, proposal, back: str) -> str:
    return layout("Pepe — ¿Está bien?", f"""
<h1>¿Está bien así?</h1>
<div class="card"><p class="readback">{e(proposal.readback)}</p></div>
<form method="post" action="/confirmar"><input type="hidden" name="id" value="{proposal.id}">
<button class="big" type="submit">{e(proposal.question)}</button></form>
<form method="post" action="/confirmar/no"><input type="hidden" name="id" value="{proposal.id}">
<input type="hidden" name="back" value="{e(back)}">
<button class="big second" type="submit">No, déjalo</button></form>
<p class="muted">Hasta que diga que sí, no se guarda nada.</p>""", menu(back, account))


def shopping(account, items: list[dict], note: str = "", bad: bool = False, removed: dict | None = None) -> str:
    rows = "".join(f'<li><span class="what">{e(i["item"])}</span>'
                   f'{_post_button("/compra/quitar", "id", i["id"], "Ya está")}</li>' for i in items)
    undo = ""
    if removed:
        undo = (f'<div class="msg good" role="status">Quitado: {e(removed["item"])}. '
                f'{_post_button("/compra/volver", "id", removed["id"], "Volver a ponerlo")}</div>')
    return layout("Pepe — La compra", f"""
<h1>La compra</h1>{_notice(note, bad)}{undo}
<p class="muted">La lista de casa, la misma que le dice Pepe.</p>
{f'<div class="card"><ul class="plain">{rows}</ul></div>' if rows else '<p class="msg">La lista está vacía.</p>'}
<form method="post" action="/compra/anadir">
<label class="f" for="item">Añadir a la lista</label>
<input class="f" id="item" name="item" maxlength="60" placeholder="leche" required>
<button class="big" type="submit">Añadir</button></form>""", menu("/compra", account))


def known(account, notes: list[dict], note: str = "", bad: bool = False) -> str:
    from kore.web.agenda import NOTE_KINDS
    rows = "".join(f'<li><span class="what">{e(n["text"])}<br><span class="muted">{e(n["kind"])}</span></span>'
                   f'{_post_button("/sabe/olvidar", "id", n["id"], "Olvídalo")}</li>' for n in notes)
    kinds = "".join(f'<option value="{k}">{e(v)}</option>' for k, v in NOTE_KINDS.items())
    return layout("Pepe — Sobre mí", f"""
<h1>Lo que Pepe sabe de usted</h1>{_notice(note, bad, good=not bad)}
<p class="muted">Lo que le ha contado a Pepe y él ha apuntado para otras conversaciones. Solo usted lo ve aquí.
Sus medicinas y citas están en «Hoy» y en «Calendario».</p>
{f'<div class="card"><ul class="plain">{rows}</ul></div>' if rows else '<p class="msg">Pepe todavía no tiene nada apuntado sobre usted.</p>'}
<h2>Contarle algo a Pepe</h2>
<form method="post" action="/sabe/anadir">
<label class="f" for="text">Lo que quiere que Pepe sepa</label>
<input class="f" id="text" name="text" maxlength="300" placeholder="Mi nieta se llama Ana" required>
<label class="f" for="kind">Qué es</label>
<select class="f" id="kind" name="kind">{kinds}</select>
<button class="big" type="submit">Seguir</button></form>""", menu("/sabe", account))


# --- family (W4) -------------------------------------------------------------------------------------


def _family_access(people: list[dict]) -> str:
    rows = "".join(
        f'<li><span class="what">{e(p["name"])}{" (" + e(p["relation"]) + ")" if p["relation"] else ""}</span>'
        + (_post_button("/familia/quitar", "contact", p["id"], "Quitar acceso") if p["access"] else
           '<span class="tag">sin acceso</span>') + "</li>" for p in people)
    return ("<h2>La familia en la página</h2><p class='muted'>Quien tiene acceso puede escribirle mensajes y "
            "proponerle citas. No ve sus medicinas, citas, notas ni conversaciones.</p>"
            + (f"<div class='card'><ul class='plain'>{rows}</ul></div>" if rows else "")
            + '<a class="big" href="/familia/invitar">Dar acceso a alguien de la familia</a>')


def invite(account, people: list[dict], note: str = "") -> str:
    options = "".join(f'<label class="check"><input type="radio" name="contact" value="{p["id"]}" required> '
                      f'{e(p["name"])}{" (" + e(p["relation"]) + ")" if p["relation"] else ""}</label>' for p in people)
    return layout("Pepe — Dar acceso", f"""
<h1>Dar acceso a la familia</h1>{_notice(note, bad=True)}
{f'<form method="post" action="/familia/invitar"><p><b>¿A quién?</b></p>{options}<button class="big" type="submit">Seguir</button></form>' if options else ''}
<h2>{'O a otra persona' if options else '¿A quién?'}</h2>
<form method="post" action="/familia/invitar">
<label class="f" for="name">Nombre</label><input class="f" id="name" name="name" maxlength="60" required>
<label class="f" for="relation">¿Quién es para usted?</label>
<input class="f" id="relation" name="relation" maxlength="40" placeholder="hija">
<button class="big" type="submit">Seguir</button></form>
<a href="/dispositivos">Volver</a>""", menu("/dispositivos", account))


def family_code(account, name: str, code: str) -> str:
    spaced = " ".join(code[i:i + 3] for i in (0, 3))
    return layout("Pepe — Código para la familia", f"""
<h1>Código para {e(name)}</h1>
<div class="card"><p class="readback" style="font-size:2rem;letter-spacing:.2em;text-align:center">{e(spaced)}</p></div>
<p>Désele a {e(name)} (en persona o por teléfono). Tiene media hora para entrar: en la página de Pepe, «¿Tiene ya
un código?», y escribirlo. Funciona una sola vez.</p>
<p class="muted">Solo funciona en la wifi de casa. Puede quitarle el acceso cuando quiera en «Dispositivos».</p>
<a class="big" href="/dispositivos">Hecho</a>""", menu("/dispositivos", account))


def family_messages(account, messages: list[dict], people: list[dict], note: str = "", bad: bool = False) -> str:
    bubbles = "".join(
        f'<div class="bubble{" me" if m["who"] == "me" else ""}">'
        + (f'<img src="/foto-familia?id={int(m["photo_id"])}" alt="Foto">' if m.get("photo_id") else "")
        + f'{e(m["text"])}<span class="when">{"Tú" if m["who"] == "me" else e(m.get("from") or "Desde casa") + ", con Pepe"}, '
        f'{e(m["at"].strftime("%d/%m %H:%M"))}{" — " + e(m["state"]) if m["state"] else ""}</span></div>' for m in messages)
    whom = "".join(f'<option value="{p["id"]}">{e(p["call_name"])}</option>' for p in people)
    whom += '<option value="both">Los dos</option>' if len(people) > 1 else ""
    return layout("Pepe — Mensajes", f"""
<h1>Mensajes</h1>{_notice(note, bad, good=not bad)}
<p class="muted">Pepe les lee tus mensajes cuando se lo piden, y te manda lo que ellos le dicten.</p>
<div class="chat">{bubbles or '<p class="muted">Todavía no hay mensajes.</p>'}</div>
<form method="post" action="/familia/escribir"><label for="t" class="f">Mensaje para casa</label>
<textarea class="say" id="t" name="text" maxlength="500" required></textarea>
<button class="big" type="submit">Enviar</button></form>
<form method="post" action="/familia/foto" enctype="multipart/form-data" class="card">
<label class="f" for="ph" style="margin-top:0">Mandar una foto</label>
<input class="f" type="file" id="ph" name="photo" accept="image/jpeg,image/png,image/webp,image/gif" required>
<label class="f" for="cap">Qué es (si quieres)</label><input class="f" id="cap" name="caption" maxlength="120">
<label class="f" for="for">Para</label><select class="f" id="for" name="for">{whom}</select>
<button class="big" type="submit">Mandar la foto</button></form>
<p class="muted">Pepe no hace caso de órdenes escritas en los mensajes ni en las fotos: solo los lee.</p>""",
                  menu("/familia/mensajes", account))


def family_home(account, d: dict) -> str:
    names = " y ".join(p["call_name"] for p in d["people"])
    meds = "".join(
        f'<div class="kv"><span>{e(x["name"])}</span><b>'
        + (e("No compartido") if not x["shared"] else "Hora no fiable" if x.get("unknown") else
           f'{x["taken"]} de {x["total"]} tomadas' if x["total"] else "Hoy no tiene")
        + "</b></div>" for x in d["doses"])
    today = "".join(f'<li><span class="time">{e(i["time"])}</span><span class="what">{e(i["text"])}</span></li>'
                    for i in d["today"])
    # Never "Todo bien": no alert only means none went out, not that KORE heard from them today (PEPE.md 6b: an
    # "all good" signal needs both to agree, and must be true).
    hero = (('<div class="card hero"><span class="label">Hoy</span><b style="font-size:1.3rem">Sin avisos</b>'
             '<span class="muted">Solo quiere decir que hoy no ha salido ningún aviso. No dice cómo están: si te '
             'preocupa, llámales.</span></div>') if d["no_alerts"] else
            '<a class="card hero" href="/familia/avisos" style="display:block;text-decoration:none"><span class="label">Hoy'
            '</span><br><b style="font-size:1.3rem">Hay un aviso</b><br><span class="muted">Míralo en Avisos.</span></a>')
    return layout("Pepe — Inicio", f"""
<p class="muted" style="margin:0">{e(d["date"])}</p>
<h1 style="margin-bottom:.2rem">{e(names)}</h1>
<p class="muted" style="margin-top:0">Solo se muestra lo que han aceptado compartir.</p>
{hero}
<section class="card"><span class="label">Pastillas</span>{meds}</section>
<section class="card"><span class="label">Hoy</span>
{f'<ul class="plain">{today}</ul>' if today else '<p class="muted">Nada que se pueda mostrar.</p>'}
<a href="/familia/calendario">Ver los próximos días</a></section>
<p class="muted">Pepe no es un sistema de alarma. En una emergencia, primero su botón de teleasistencia y el 112.</p>""",
                  menu("/", account))


def family_calendar(account, days: list[dict]) -> str:
    from kore.brain.fallback import MONTHS, WEEKDAYS
    blocks = []
    for i, d in enumerate(days):
        label = ("Hoy · " if i == 0 else "") + f'{WEEKDAYS[d["date"].weekday()]} {d["date"].day}'
        rows = "".join(f'<li><span class="time">{e(x["time"])}</span><span class="what">{e(x["text"])}'
                       f'{"<span class=sub>" + e(x["note"]) + "</span>" if x.get("note") else ""}</span></li>'
                       for x in d["items"])
        blocks.append(f'<section class="card"><span class="label">{e(label)}</span>'
                      f'{f"<ul class=plain>{rows}</ul>" if rows else "<p class=muted>Nada especial.</p>"}</section>')
    return layout("Pepe — Calendario", f"""
<h1>Calendario</h1>{"".join(blocks)}
<a class="big" href="/familia/cita">Proponer una cita</a>
<p class="muted">Ellos la ven en su página y deciden si la apuntan; Pepe se la recordará. Las medicinas solo las cambian
ellos, hablando con Pepe.</p>""", menu("/familia/calendario", account))


def family_alerts(account, d: dict) -> str:
    kinds = "".join(f"<li>{e(k)}</li>" for k in d["kinds"])
    recent = "".join(f'<li><span class="what"><b>{e(r["what"])}</b> · {e(r["at"].strftime("%a %d %H:%M"))}'
                     f'<span class="sub">{e(r["text"])}</span></span><span class="pill">{e(r["state"])}</span></li>'
                     for r in d["recent"])
    return layout("Pepe — Avisos", f"""
<h1>Avisos</h1>
<section class="card"><span class="label">Recibes avisos de</span>
{f'<ul class="plain">{kinds}</ul>' if kinds else '<p class="muted">De momento ninguno: no eres su contacto de ayuda.</p>'}
<p class="muted">Lo eligen ellos, y lo pueden cambiar hablando con Pepe.</p></section>
<section class="card"><span class="label">Esta semana</span>
{f'<ul class="plain">{recent}</ul>' if recent else '<p class="muted">Ningún aviso.</p>'}</section>
<section class="card tint"><b>Cuando llega un aviso de ayuda</b><p>Llámales primero. Si no contestan, llama al 112.
Pepe no es un sistema de alarma.</p></section>""", menu("/familia/avisos", account))


def family_propose(account, people: list[dict], mine: list[dict], today: str, f: dict | None = None,
                   note: str = "", bad: bool = False) -> str:
    f = f or {}
    who = "".join(f'<label class="check"><input type="radio" name="for" value="{p["id"]}" required'
                  f'{" checked" if f.get("for") == str(p["id"]) else ""}> {e(p["call_name"])}</label>' for p in people)
    who += (f'<label class="check"><input type="radio" name="for" value="both" required'
            f'{" checked" if f.get("for") == "both" else ""}> Los dos</label>') if len(people) > 1 else ""
    rows = "".join(
        f'<li><span class="what"><b>{e(p["title"])}</b><br><span class="muted">{e(p["when"])}, para {e(p["for"])}'
        f'</span></span><span class="tag">{e(p["state"])}</span>'
        + (_post_button("/familia/cita/retirar", "id", p["id"], "Retirar") if p["open"] else "") + "</li>"
        for p in mine)
    return layout("Pepe — Proponer una cita", f"""
<h1>Proponer una cita</h1>{_notice(note, bad, good=not bad)}
<p class="muted">La persona la verá en su página y decidirá si la apunta. Hasta entonces no se apunta nada.</p>
<form method="post" action="/familia/cita">
<p><b>¿Para quién?</b></p>{who}
<label class="f" for="title">¿Qué es?</label>
<input class="f" id="title" name="title" maxlength="120" placeholder="Médico de cabecera" value="{e(f.get('title', ''))}"
 required>
{_date_time(f, today)}
<label class="f" for="where">¿Dónde? (si quiere)</label>
<input class="f" id="where" name="where" maxlength="120" value="{e(f.get('where', ''))}">
<button class="big" type="submit">Proponer</button></form>
{f'<h2>Sus propuestas</h2><div class="card"><ul class="plain">{rows}</ul></div>' if rows else ''}""",
                  menu("/familia/cita", account))


# --- Carmen and Antonio's app (W-design: Inicio, Pepe, Agenda, Familia, Pastillas, Ayuda, Ajustes) ---------------


def elder_home(account, d: dict) -> str:
    parts = [f'<h1 style="margin-bottom:.2rem">Hola, {e(account.name)}</h1>'
             f'<p class="muted" style="margin:0">{e(d["date"])}<br>{e(d["time"])}{" · " + e(d["weather"]) if d.get("weather") else ""}</p>']
    nxt = d.get("next")
    if nxt:
        when = f" · {e(nxt['time'])}" if nxt.get("time") else ""
        call = (f'<a href="tel:{e(nxt["phone"])}">Llamar a {e(nxt["call"])}</a>' if nxt.get("phone") else "")
        parts.append(f'<div class="next"><span class="label">Lo próximo{when}</span>'
                     f'<span class="what">{e(nxt["text"])}</span>{call}</div>')
    p = d["pills"]
    if not p["clock_ok"]:
        parts.append('<p class="msg bad">La hora de Pepe no es fiable ahora mismo, así que las pastillas no se '
                     'muestran. Mira el pastillero.</p>')
    elif p["doses"]:
        more = f" · la próxima a las {e(p['next'])}" if p["next"] else ""
        parts.append(f'<a class="big-card" href="/pastillas"><span><b>Pastillas de hoy</b><br>'
                     f'<span class="muted">{p["taken"]} de {len(p["doses"])} tomadas{more}</span></span>'
                     f'<span class="chev" aria-hidden="true">›</span></a>')
    parts.append(f'<a class="talk" href="/hablar">{icon("mic", 30)}Hablar con Pepe</a>')
    if d.get("new_photos"):
        n = len(d["new_photos"])
        who = " y ".join(sorted({x["from"] for x in d["new_photos"]}))
        cap = d["new_photos"][0]["caption"]
        parts.append(f'<a class="big-card" href="/fotos"><span><b>{e(who)} {"ha" if len({x["from"] for x in d["new_photos"]}) == 1 else "han"} '
                     f'enviado {n} foto{"s" if n > 1 else ""}</b>{"<br><span class=muted>" + e(cap) + "</span>" if cap else ""}'
                     f'</span><span class="chev" aria-hidden="true">›</span></a>')
    if d.get("unread"):
        parts.append(f'<a class="big-card" href="/mi-familia"><span><b>{t("Tienes", "Tiene")} '
                     f'{d["unread"]} mensaje{"s" if d["unread"] > 1 else ""} de la familia</b></span>'
                     f'<span class="chev" aria-hidden="true">›</span></a>')
    if d.get("proposals"):
        names = " y ".join(sorted({x["from"] for x in d["proposals"]}))
        parts.append(f'<a class="big-card" href="/calendario#propuestas"><span><b>{e(names)} '
                     f'{t("te", "le")} propone una cita</b><br><span class="muted">Míralo y decide</span></span>'
                     f'<span class="chev" aria-hidden="true">›</span></a>')
    for m in d.get("medicine_proposals") or []:
        parts.append(f'<div class="card tint"><b>Lea propone tomar {e(m["name"])} a las {e(", ".join(m["times"]))}'
                     f'</b><p class="muted" style="margin:.3rem 0 0">No cambia nada hasta que {t("se lo digas", "se lo diga")} '
                     f'a Pepe. Lo que diga {t("tu", "su")} médico es lo que vale.</p>'
                     f'<form method="post" action="/propuesta-lea/no"><input type="hidden" name="id" value="{int(m["id"])}">'
                     f'<button class="big second" type="submit">No, gracias</button></form></div>')
    if d.get("shopping"):
        parts.append(f'<a class="big-card" href="/compra"><span><b>La compra</b><br><span class="muted">'
                     f'{e(" · ".join(d["shopping"][:6]))}</span></span><span class="chev" aria-hidden="true">›</span></a>')
    parts.append('<a class="big second" href="/ayuda" style="margin-top:1.2rem">Pedir ayuda</a>')
    return layout("Pepe — Inicio", "".join(parts), menu("/", account))


def elder_agenda(account, days: list[dict], chosen: int, items: list[dict]) -> str:
    tabs_html = "".join(f'<a href="/agenda?dia={x["n"]}" class="{"here" if x["n"] == chosen else ""}"'
                        f'{" aria-current=page" if x["n"] == chosen else ""}>{e(x["label"])}<br>{x["day"]}</a>'
                        for x in days)
    rows = "".join(
        f'<div class="item {i["kind"]}{" done" if i["done"] else ""}"><b>{e(i["time"])}</b><span>{e(i["text"])}'
        + (' · <span class="ok-text">tomada</span>' if i["kind"] == "med" and i["done"] else "")
        + (f'<span class="sub">{e(i["note"])}</span>' if i["note"] and i["kind"] != "med" else "") + '</span></div>'
        for i in items) or '<p class="muted">Ese día no hay nada apuntado.</p>'
    return layout("Pepe — Agenda", f"""
<h1>Agenda</h1>
<nav class="day-tabs" aria-label="Día">{tabs_html}</nav>
{rows}
<div class="pair" style="margin-top:1.2rem"><a class="big second" href="/pastillas">Mis pastillas</a>
<a class="big" href="/hablar">Añadir algo</a></div>
<p class="muted">Para añadir, {t("díselo", "dígaselo")} a Pepe: «Recuérdame el dentista el martes a las 10».
{t("También puedes", "También puede")} <a href="/calendario">apuntarlo o cambiarlo aquí</a>, o mirar
<a href="/compra">la compra</a>.</p>""", menu("/agenda", account))


def elder_pills(account, p: dict, note: str = "") -> str:
    if not p["clock_ok"]:
        body = ('<p class="msg bad">La hora de Pepe no es fiable ahora mismo, así que no se pueden mostrar ni apuntar '
                f'las pastillas. {t("Mira", "Mire")} el pastillero.</p>')
    else:
        cards = []
        for d in p["doses"]:
            if d["state"] == "taken":
                state = f'<span class="ok-text">Tomada{" a las " + e(d["taken_at"]) if d.get("taken_at") else ""}</span>'
            elif d["med_id"]:
                state = '<span style="color:var(--accent);font-weight:700">Pendiente</span>'
            elif d["state"] == "upcoming":
                state = '<span class="muted">Más tarde</span>'
            else:
                state = '<span class="muted">Sin apuntar</span>'
            button = (f'<form method="post" action="/pastillas/tomada"><input type="hidden" name="id" value="{d["med_id"]}">'
                      f'<button class="big" type="submit">Ya me la he tomado</button></form>' if d["med_id"] else "")
            cards.append(f'<div class="card{" tint" if d["med_id"] else ""}"><div class="kv"><b style="font-size:1.05rem">'
                         f'{e(d["time"])}</b>{state}</div><div>{e(d["name"])}</div>{button}</div>')
        body = "".join(cards) or f'<p class="msg">Hoy no {t("tienes", "tiene")} pastillas apuntadas.</p>'
    notice = f'<p class="msg good" role="status">{e(note)}</p>' if note else ""
    return layout("Pepe — Pastillas", f"""
<p><a href="/agenda">‹ Agenda</a></p>
<h1>{t("Mis pastillas de hoy", "Mis pastillas de hoy")}</h1>{notice}{body}
<div class="card">¿No {t("te acuerdas si la tomaste", "se acuerda de si la tomó")}? <b>No {t("tomes", "tome")} otra.</b>
{t("Mira", "Mire")} el pastillero o {t("pregúntale", "pregúntele")} a Pepe.</div>
<p class="muted">«Sin apuntar» quiere decir que Pepe no lo sabe, no que no se haya tomado.</p>""",
                  menu("/pastillas", account))


def elder_family(account, messages: list[dict], people: list[dict], note: str = "", bad: bool = False) -> str:
    notice = f'<p class="msg{" bad" if bad else " good"}" role="status">{e(note)}</p>' if note else ""
    msgs = "".join(
        f'<div class="card tint"><span class="label" style="color:var(--accent)">Mensaje nuevo de {e(m["from"])}</span>'
        f'<p style="font-size:1.05rem;margin:.4rem 0">«{e(m["text"])}»</p>'
        f'<form method="post" action="/mi-familia/responder"><input type="hidden" name="contact" value="{m["contact_id"]}">'
        f'<label class="f" for="r{m["contact_id"]}">{t("Responder", "Responder")} a {e(m["from"])}</label>'
        f'<textarea class="say" id="r{m["contact_id"]}" name="text" maxlength="500" required></textarea>'
        f'<button class="big" type="submit">Responder</button></form></div>' for m in messages)
    cards = []
    for c in people:
        who = f'{e(c["name"])}{", " + t("tu", "su") + " " + e(c["relation"]) if c["relation"] else ""}'
        buttons = []
        if c["phone"]:
            buttons.append(f'<a class="big" href="tel:{e(c["phone"])}">Llamar</a>')
            buttons.append(f'<a class="big second" href="https://wa.me/{e(c["phone"].lstrip("+"))}" '
                           f'rel="noopener noreferrer">Videollamada</a>')
        write = (f'<details><summary class="small" style="margin-top:.5rem">Escribir a {e(c["name"])}</summary>'
                 f'<form method="post" action="/mi-familia/responder"><input type="hidden" name="contact" value="{c["id"]}">'
                 f'<label class="f" for="w{c["id"]}">Mensaje</label>'
                 f'<textarea class="say" id="w{c["id"]}" name="text" maxlength="500" required></textarea>'
                 f'<button class="big" type="submit">Seguir</button></form></details>' if c["can_write"] else "")
        cards.append(f'<div class="card"><b style="font-size:1.05rem">{who}</b>'
                     f'{"<div class=pair>" + "".join(buttons) + "</div>" if buttons else ""}{write}</div>')
    wa = ('<p class="muted">La videollamada se abre en WhatsApp.</p>' if any(c["phone"] for c in people) else "")
    return layout("Pepe — Familia", f"""
<h1>Familia</h1>{notice}{msgs}
{"".join(cards) or '<p class="msg">Todavía no hay familia apuntada. ' + t("Díselo", "Dígaselo") + ' a Pepe.</p>'}
<a class="big-card" href="/fotos"><span><b>Fotos</b></span><span class="chev" aria-hidden="true">›</span></a>
{wa}""", menu("/mi-familia", account))


def elder_help(account, names: list[str], result: str = "") -> str:
    who = " y ".join(names)
    if result:
        body = f'<p class="msg" role="status" style="font-size:1.15rem">{e(result)}</p><a class="big second" href="/">Volver</a>'
    elif names:
        body = (f'<p style="font-size:1.05rem">Si {t("dices", "dice")} que sí, le enviamos un aviso a {e(who)} para que '
                f'{t("te", "le")} llame enseguida.</p>'
                f'<form method="post" action="/ayuda"><button class="big huge" type="submit">Sí, avisar a {e(who)}</button></form>'
                f'<a class="big second" href="/">No, ha sido sin querer</a>')
    else:
        body = (f'<p class="msg bad">Todavía no hay nadie apuntado para avisar. {t("Díselo", "Dígaselo")} a Pepe para '
                'apuntarlo.</p>')
    return layout("Pepe — Ayuda", f"""
<p><a href="/">‹ Inicio</a></p>
<h1>¿{t("Necesitas", "Necesita")} ayuda, {e(account.name)}?</h1>
{body}
<div class="card" style="margin-top:2rem"><b>Si es una emergencia</b>
<p>{t("Pulsa tu", "Pulse su")} botón de teleasistencia o {t("llama", "llame")} al 112.</p>
<a class="big" href="tel:112" style="background:var(--accent-dark);border-color:var(--accent-dark)">Llamar al 112</a></div>
""", menu("/ayuda", account))


def elder_settings(account, size: str, note: str = "") -> str:
    notice = f'<p class="msg good" role="status">{e(note)}</p>' if note else ""
    sizes = "".join(f'<form method="post" action="/ajustes/letra"><input type="hidden" name="size" value="{k}">'
                    f'<button class="big{"" if k == size else " second"}" type="submit"'
                    f'{" aria-pressed=true" if k == size else ""}>{label}</button></form>'
                    for k, label in (("grande", "Grande"), ("mayor", "Mayor"), ("enorme", "Enorme")))
    return layout("Pepe — Ajustes", f"""
<p><a href="/">‹ Inicio</a></p>
<h1>Ajustes</h1>{notice}
<h2>Tamaño de la letra</h2><div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.5rem">{sizes}</div>
<h2>Lo mío</h2>
<a class="big-card" href="/sabe"><span><b>Lo que Pepe sabe de {t("ti", "usted")}</b></span><span class="chev" aria-hidden="true">›</span></a>
<a class="big-card" href="/lo-que-ven"><span><b>Lo que ve {t("tu", "su")} familia y Lea</b></span><span class="chev" aria-hidden="true">›</span></a>
<a class="big-card" href="/dispositivos"><span><b>Móviles conectados y acceso de la familia</b></span><span class="chev" aria-hidden="true">›</span></a>
<p class="muted">Cómo habla Pepe (más despacio, más alto, más corto) y los permisos se cambian hablando con Pepe.</p>
<h2>Pepe en la pantalla de inicio</h2>
<div class="card"><p>Para abrir Pepe con un toque, como cualquier aplicación:</p>
<p><b>iPhone:</b> en Safari, {t("toca", "toque")} el botón Compartir (el cuadrado con la flecha) y luego
«Añadir a pantalla de inicio». Al abrirlo desde el icono, Pepe {t("te", "le")} pedirá otra vez el código, solo esa vez.</p>
<p><b>Android:</b> en Chrome, {t("toca", "toque")} el menú (los tres puntos) y luego «Añadir a pantalla de inicio»
o «Instalar aplicación».</p></div>
<form method="post" action="/salir"><button class="big second" type="submit">Salir de este móvil</button></form>
""", menu("/ajustes", account))


def elder_who_sees(account, d: dict) -> str:
    def block(title, rows):
        return (f'<h2>{e(title)}</h2><div class="card"><ul class="plain">' + "".join(
            f'<li><span class="what">{e(label)}</span><span class="pill {"ok" if yes else ""}">{"Sí" if yes else "No"}'
            f'</span></li>' for label, yes in rows) + "</ul></div>")
    return layout("Pepe — Lo que ven", f"""
<p><a href="/ajustes">‹ Ajustes</a></p>
<h1>Lo que ven los demás</h1>
{block(t("Tu familia", "Su familia"), d["family"])}
{block("Lea, que cuida de que Pepe funcione", d["lea"])}
{block(t("La otra persona de casa", "La otra persona de casa"), d["partner"])}
<p class="muted">Para cambiar cualquiera, {t("díselo", "dígaselo")} a Pepe: «Pepe, no quiero que Lea vea mis conversaciones».
{t("Tus", "Sus")} conversaciones y notas nunca se ven sin {t("tu", "su")} sí.</p>""", menu("/ajustes", account))


def elder_photos(account, family: list[dict], mine: list[int], people: list[dict], note: str = "",
                 bad: bool = False) -> str:
    notice = f'<p class="msg{" bad" if bad else " good"}" role="status">{e(note)}</p>' if note else ""
    fam = "".join(
        f'<div class="card"><img src="/foto-familia?id={x["id"]}" alt="Foto de {e(x["from"])}" '
        f'style="width:100%;border-radius:14px"><p><b>{e(x["from"])}</b> · {e(x["at"].strftime("%d/%m"))}'
        f'{" · «" + e(x["caption"]) + "»" if x["caption"] else ""}</p><div class="pair">'
        f'<a class="big second" href="/foto-familia?id={x["id"]}">Más grande</a>'
        f'<form method="post" action="/fotos/hablar"><input type="hidden" name="id" value="{x["id"]}">'
        f'<button class="big" type="submit">Hablar de ella</button></form></div></div>' for x in family)
    grid = "".join(f'<a href="/foto?id={i}"><img src="/foto?id={i}" alt="Foto que {t("mandaste", "mandó")} a Pepe" '
                   f'style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:12px"></a>' for i in mine)
    to = "".join(f'<option value="{p["id"]}">{e(p["name"])}</option>' for p in people if p["can_write"])
    send = (f"""<form method="post" action="/fotos/enviar" enctype="multipart/form-data" class="card">
<label class="f" for="ph" style="margin-top:0">Enviar una foto a la familia</label>
<input class="f" type="file" id="ph" name="photo" accept="image/jpeg,image/png,image/webp,image/gif" required>
<label class="f" for="to">Para</label><select class="f" id="to" name="to">{to}</select>
<label class="f" for="cap">Qué es ({t("si quieres", "si quiere")})</label><input class="f" id="cap" name="caption" maxlength="120">
<button class="big" type="submit">Seguir</button>
<p class="muted">{t("Eliges", "Elige")} la foto de {t("tu", "su")} móvil. Solo la ve la persona que {t("digas", "diga")}.</p></form>"""
            if to else "")
    return layout("Pepe — Fotos", f"""
<p><a href="/mi-familia">‹ Familia</a></p>
<h1>Fotos</h1>{notice}
{fam or '<p class="muted">La familia todavía no ha mandado fotos.</p>'}
{send}
{f'<h2>Las que {t("mandaste", "mandó")} a Pepe</h2><div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.5rem">{grid}</div>' if grid else ''}
<p class="muted">Las fotos se guardan solo en Pepe, sin el lugar donde se hicieron.</p>""", menu("/fotos", account))
