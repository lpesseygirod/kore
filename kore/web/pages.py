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
button.small, a.small { text-decoration: none; display: inline-block; font: inherit; font-size: .9rem; padding: .3rem .7rem; border-radius: 10px;
      border: 1px solid var(--line); background: var(--bg); color: var(--fg); cursor: pointer; }
input.code { font: inherit; font-size: 2rem; letter-spacing: .4em; text-align: center; width: 100%;
      padding: .6rem; border-radius: 14px; border: 2px solid var(--line); background: var(--card); color: var(--fg); }
.chat { display: flex; flex-direction: column; gap: .5rem; margin: 1rem 0; }
.bubble { max-width: 85%; padding: .6rem .85rem; border-radius: 16px; border: 1px solid var(--line);
          background: var(--card); white-space: pre-wrap; overflow-wrap: anywhere; }
.bubble.me { align-self: flex-end; background: var(--bg); border-color: var(--accent); }
.bubble img { display: block; max-width: 100%; height: auto; border-radius: 10px; margin-bottom: .3rem; }
.bubble .when { display: block; font-size: .75rem; color: var(--muted); margin-top: .2rem; }
textarea.say { font: inherit; width: 100%; min-height: 5.5rem; padding: .7rem; border-radius: 14px;
               border: 2px solid var(--line); background: var(--card); color: var(--fg); }
.msg { padding: .8rem 1rem; border-radius: 14px; border: 1px solid var(--line); background: var(--card); }
.msg.bad { border-color: var(--warn); }
.msg.good { border-color: var(--ok); }
label.f { display: block; margin: .8rem 0 .25rem; font-weight: 600; }
input.f, select.f { font: inherit; width: 100%; padding: .6rem .7rem; border-radius: 12px; border: 2px solid var(--line);
      background: var(--card); color: var(--fg); }
.check { display: flex; gap: .5rem; align-items: center; margin: .6rem 0; font-size: 1rem; }
.check input { width: 1.4rem; height: 1.4rem; }
.days { display: flex; flex-wrap: wrap; gap: .3rem 1rem; }
.readback { font-size: 1.2rem; font-weight: 600; }
ul.plain li form { margin-left: auto; }
ul.plain li .what { flex: 1; }
"""


def e(value) -> str:
    return escape("" if value is None else str(value), quote=True)


def layout(title: str, body: str, nav: str = "") -> str:
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="color-scheme" content="light dark"><title>{e(title)}</title>'
            f'<style>{CSS}</style></head><body>{nav}<main>{body}</main></body></html>')


def menu(here: str, account) -> str:
    mine = [("/hablar", "Hablar"), ("/calendario", "Calendario"), ("/compra", "La compra"), ("/sabe", "Sobre mí")]
    if account.role == "family":
        items = [("/", "Mensajes"), ("/familia/cita", "Proponer una cita"), ("/dispositivos", "Dispositivos")]
    else:
        items = [("/", "Hoy")] + (mine if account.role == "person" else []) + [("/dispositivos", "Dispositivos")]
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


# --- calendar, shopping list, what Pepe knows (W3) ----------------------------------------------

DONE = {"apuntado": "Hecho. Pepe lo recordará en voz alta cuando toque.", "quitado": "Hecho, quitado.",
        "olvidado": "Hecho. Pepe lo ha olvidado.", "anulado": "No se ha cambiado nada.",
        "enviado": "Mensaje enviado. Pepe les dirá que tienen un mensaje suyo.",
        "propuesta": "Propuesta enviada. La verán en su página y decidirán si la apuntan."}


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
<button class="big" type="submit">No, déjalo</button></form>
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


def family_home(account, messages: list[dict], note: str = "", bad: bool = False) -> str:
    bubbles = "".join(
        f'<div class="bubble{" me" if m["who"] == "me" else ""}">{e(m["text"])}'
        f'<span class="when">{"Usted" if m["who"] == "me" else "Desde casa"}, {e(m["at"].strftime("%d/%m %H:%M"))}'
        f'{" — " + e(m["state"]) if m["state"] else ""}</span></div>' for m in messages)
    return layout("Pepe — Mensajes", f"""
<h1>Hola, {e(account.name)}</h1>{_notice(note, bad, good=not bad)}
<p class="muted">Los mensajes que le han mandado desde casa con Pepe, y los suyos. Pepe les dice que tienen un mensaje
y se lo lee cuando se lo piden.</p>
<div class="chat">{bubbles or '<p class="muted">Todavía no hay mensajes.</p>'}</div>
<form method="post" action="/familia/escribir"><label for="t" class="muted">Escribir a casa</label>
<textarea class="say" id="t" name="text" maxlength="500" required></textarea>
<button class="big" type="submit">Enviar</button></form>
<p class="muted">Pepe no hace caso de órdenes escritas en los mensajes: solo los lee.</p>""", menu("/", account))


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
