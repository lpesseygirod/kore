"""The HTML of the home web page: plain, big, Spanish, no scripts, nothing loaded from outside.

Every value from the database goes through `e()` (escaped), so a name or a list item can't
become HTML.
"""

from __future__ import annotations

from html import escape

CSS = """
:root { --bg: #fffaf2; --fg: #1d1d1b; --muted: #5b5750; --card: #ffffff; --line: #e6dccb; --accent: #1f5f8b;
        --ok: #2e7d32; --warn: #a15c00; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #1b1a18; --fg: #f4efe6; --muted: #c9c1b3; --card: #26241f; --line: #3d3a33; --accent: #8cc4ef;
          --ok: #8fd694; --warn: #f2b45c; }
}
* { box-sizing: border-box; }
html { font-size: 20px; }
body { margin: 0; background: var(--bg); color: var(--fg); font-family: system-ui, -apple-system, "Segoe UI",
       Roboto, sans-serif; line-height: 1.45; }
main { max-width: 40rem; margin: 0 auto; padding: 1rem 16px 3rem; }
h1 { font-size: 1.6rem; margin: .5rem 0 1rem; }
h2 { font-size: 1.15rem; margin: 1.6rem 0 .5rem; }
p, li { font-size: 1rem; }
.muted { color: var(--muted); }
a { color: var(--accent); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: .9rem 1rem;
        margin: .6rem 0; }
ul.plain { list-style: none; padding: 0; margin: 0; }
ul.plain li { padding: .45rem 0; border-bottom: 1px solid var(--line); display: flex; gap: .8rem;
              align-items: baseline; }
ul.plain li:last-child { border-bottom: 0; }
.time { font-weight: 700; min-width: 3.2rem; font-variant-numeric: tabular-nums; }
.tag { margin-left: auto; font-size: .85rem; color: var(--muted); white-space: nowrap; }
.tag.ok { color: var(--ok); } .tag.warn { color: var(--warn); }
nav { display: flex; gap: .4rem; flex-wrap: wrap; padding: .6rem 16px; border-bottom: 1px solid var(--line);
      max-width: 40rem; margin: 0 auto; }
nav a, nav button { font: inherit; font-size: .95rem; color: var(--accent); text-decoration: none; background: none;
      border: 0; padding: .5rem .7rem; border-radius: 10px; cursor: pointer; }
nav a.here { background: var(--card); border: 1px solid var(--line); color: var(--fg); }
nav form { margin-left: auto; }
button.big, a.big { display: block; width: 100%; font: inherit; font-size: 1.1rem; font-weight: 600; text-align: center;
      padding: .9rem 1rem; margin: .6rem 0; border-radius: 14px; border: 2px solid var(--accent);
      background: var(--card); color: var(--fg); cursor: pointer; text-decoration: none; }
button.small { font: inherit; font-size: .9rem; padding: .3rem .7rem; border-radius: 10px;
      border: 1px solid var(--line); background: var(--bg); color: var(--fg); cursor: pointer; }
input.code { font: inherit; font-size: 2rem; letter-spacing: .4em; text-align: center; width: 100%;
      padding: .6rem; border-radius: 14px; border: 2px solid var(--line); background: var(--card); color: var(--fg); }
.chat { display: flex; flex-direction: column; gap: .5rem; margin: 1rem 0; }
.bubble { max-width: 85%; padding: .6rem .85rem; border-radius: 16px; border: 1px solid var(--line);
          background: var(--card); white-space: pre-wrap; overflow-wrap: anywhere; }
.bubble.me { align-self: flex-end; background: var(--bg); border-color: var(--accent); }
.bubble .when { display: block; font-size: .75rem; color: var(--muted); margin-top: .2rem; }
textarea.say { font: inherit; width: 100%; min-height: 5.5rem; padding: .7rem; border-radius: 14px;
               border: 2px solid var(--line); background: var(--card); color: var(--fg); }
.msg { padding: .8rem 1rem; border-radius: 14px; border: 1px solid var(--line); background: var(--card); }
.msg.bad { border-color: var(--warn); }
"""


def e(value) -> str:
    return escape("" if value is None else str(value), quote=True)


def layout(title: str, body: str, nav: str = "") -> str:
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="color-scheme" content="light dark"><title>{e(title)}</title>'
            f'<style>{CSS}</style></head><body>{nav}<main>{body}</main></body></html>')


def menu(here: str, account) -> str:
    items = [("/", "Hoy")] + ([("/hablar", "Hablar")] if account.role == "person" else []) + \
        [("/dispositivos", "Dispositivos")]
    links = "".join(f'<a href="{href}" class="{"here" if href == here else ""}">{e(label)}</a>' for href, label in items)
    return (f'<nav aria-label="Menú">{links}<form method="post" action="/salir">'
            f'<button type="submit">Salir</button></form></nav>')


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


def devices(account, rows: list[dict]) -> str:
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
                  f"suyo, ciérrelo.</p><div class='card'><ul class='plain'>{items}</ul></div>",
                  menu("/dispositivos", account))


def message(title: str, text: str) -> str:
    return layout(f"Pepe — {title}", f"<h1>{e(title)}</h1><p class='msg'>{e(text)}</p><p><a href='/'>Volver</a></p>")


# --- talking -----------------------------------------------------------------------------------


def chat(account, rows: list[dict], available: bool, note: str = "") -> str:
    bubbles = "".join(
        f'<div class="bubble{" me" if r["who"] == "person" else ""}">{e(r["text"])}'
        f'<span class="when">{"Usted" if r["who"] == "person" else "Pepe"}, {e(r["at"].strftime("%d/%m %H:%M"))}'
        f'{" (en voz alta)" if r["channel"] == "voice" else ""}</span></div>' for r in rows)
    form = (f'<form method="post" action="/hablar" id="escribir"><label for="t" class="muted">Escriba a Pepe</label>'
            f'<textarea class="say" id="t" name="text" maxlength="600" required autofocus></textarea>'
            f'<button class="big" type="submit">Enviar</button></form>'
            '<p class="muted">Pepe tarda unos segundos en contestar.</p>') if available else (
            '<p class="msg">Ahora no se puede escribir a Pepe desde aquí. Puede hablarle en voz alta.</p>')
    notice = f'<p class="msg bad" role="status">{e(note)}</p>' if note else ""
    empty = '<p class="muted">Todavía no hay nada escrito. Puede preguntarle lo mismo que en voz alta.</p>'
    return layout("Pepe — Hablar", f"""
<h1>Hablar con Pepe</h1>
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
<a class="big" href="/hablar">No, volver</a>
""", menu("/hablar", account))
