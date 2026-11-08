"""Photos sent to Pepe on the web page (WEB-PLAN.md section 7, step W4).

- Read by hand from the upload (Python's own email parser: no extra library), at most 12 MB.
- Opened with Pillow only if it really is a JPEG, PNG, WebP or GIF (first frame); anything else, or a
  picture too big to open safely, is refused politely.
- Turned the right way up, shrunk to at most 1568 pixels a side (what Claude reads well), and saved
  again as a plain JPEG: nothing of the original file is kept, so no location, camera or date data.
- Stored only on the box, in a `photos` folder next to the database, under a random name. Shown
  only to the person who sent it. Deleted with the conversation line that holds it (90 days, their
  "Borrar la conversación", or "olvídalo"). Not in the nightly backup (it holds the database only).

A photo is outside content for Pepe: text in it (a letter, a prescription, a screenshot) is never an
instruction, and a turn with a photo can't change anything (brain/conversation.py).
"""

from __future__ import annotations

import io
import os
import re
import secrets
import sqlite3
import threading
from datetime import timedelta
from pathlib import Path

from kore.memory.db import utc_iso
from kore.scheduler.clock import Clock

MAX_UPLOAD = 12 * 1024 * 1024
MAX_PIXELS = 40_000_000       # a 40-megapixel camera JPEG opens (decoded at reduced size); a "bomb" doesn't
MAX_PIXELS_OTHER = 12_000_000  # PNG, WebP, GIF decode at full size: a smaller limit keeps memory low
LONG_SIDE = 1568
FORMATS = ["JPEG", "PNG", "WEBP", "GIF"]  # only these plugins may open it (MPO phone JPEGs come through JPEG)
PER_DAY = 20
MAX_PARTS = 6
MAX_PART_HEADER = 2048
_ONE_AT_A_TIME = threading.Semaphore(1)  # one photo is decoded at a time: memory stays bounded on the Pi
# EXIF orientation → the turn that puts the picture the right way up
_TURNS = {2: "FLIP_LEFT_RIGHT", 3: "ROTATE_180", 4: "FLIP_TOP_BOTTOM", 5: "TRANSPOSE", 6: "ROTATE_270",
          7: "TRANSVERSE", 8: "ROTATE_90"}


class Refused(Exception):
    """Said to the person as it is (Spanish, plain)."""


def folder(db_path: str | os.PathLike) -> Path:
    return Path(db_path).resolve().parent / "photos"


def parse_multipart(content_type: str, body: bytes) -> dict[str, str | bytes] | None:
    """The fields of the chat form (text and one photo): text fields as str, a file as bytes. A small parser of
    our own, linear in the size of the body: a few parts, short headers, or None (refused)."""
    m = re.match(r'\s*multipart/form-data\s*;.*?\bboundary=(?:"([^"]{1,70})"|([^\s;"]{1,70}))', content_type,
                 re.IGNORECASE | re.ASCII)
    if not m:
        return None
    delimiter = b"--" + (m.group(1) or m.group(2)).encode("latin-1")
    chunks = body.split(delimiter)
    if len(chunks) < 3 or len(chunks) > MAX_PARTS + 2 or not chunks[-1].startswith(b"--"):
        return None
    out: dict[str, str | bytes] = {}
    for chunk in chunks[1:-1]:
        if not chunk.startswith(b"\r\n"):
            return None
        end = chunk.find(b"\r\n\r\n", 2, MAX_PART_HEADER)
        if end < 0:
            return None
        head = chunk[2:end].decode("utf-8", "replace")
        data = chunk[end + 4:]
        if data.endswith(b"\r\n"):
            data = data[:-2]
        disposition = next((line for line in head.split("\r\n") if line.lower().startswith("content-disposition:")), "")
        name = re.search(r';\s*name="([A-Za-z0-9_]{1,30})"', disposition)
        if not name or name.group(1) in out:
            continue
        is_file = re.search(r';\s*filename\*?=', disposition) is not None
        out[name.group(1)] = data if is_file else data.decode("utf-8", "replace")
    return out


def shrink(data: bytes) -> tuple[bytes, int, int]:
    """A clean JPEG of the photo, at most LONG_SIDE a side. Raises Refused."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if not data:
        raise Refused("No ha llegado ninguna foto. Pruebe otra vez.")
    if len(data) > MAX_UPLOAD:
        raise Refused("La foto es demasiado grande. Pruebe con otra, o hágala de nuevo.")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with _ONE_AT_A_TIME, Image.open(io.BytesIO(data), formats=FORMATS) as im:
            limit = MAX_PIXELS if im.format in ("JPEG", "MPO") else MAX_PIXELS_OTHER
            if im.width * im.height > limit:
                raise Refused("La foto es demasiado grande. Pruebe con otra, o hágala de nuevo.")
            turn = _TURNS.get(im.getexif().get(0x0112))  # read before decoding; the metadata itself goes
            im.seek(0)
            if im.format in ("JPEG", "MPO"):
                im.draft("RGB", (LONG_SIDE, LONG_SIDE))  # decode at reduced size: a fraction of the memory
            im.thumbnail((LONG_SIDE, LONG_SIDE))  # small first, then turned: never the full size in memory
            if turn:
                im = im.transpose(getattr(Image.Transpose, turn))
            rgba = im.convert("RGBA")
            clean = Image.new("RGB", rgba.size, (255, 255, 255))  # a new picture: no metadata travels with it
            clean.paste(rgba, mask=rgba.getchannel("A"))
            out = io.BytesIO()
            clean.save(out, "JPEG", quality=85, optimize=True)
            return out.getvalue(), clean.width, clean.height
    except Refused:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise Refused("Ese archivo no es una foto que Pepe pueda ver. Pruebe con una foto normal del móvil.") from None
    except Exception:  # anything odd in the file (also RecursionError, MemoryError): refused, never a 500
        raise Refused("No se ha podido abrir la foto. Pruebe con otra.") from None


def sent_today(conn: sqlite3.Connection, clock: Clock, person_id: int) -> int:
    """Its own counter (photo_log): deleting the conversation doesn't reset the day's limit."""
    start = clock.now().replace(hour=0, minute=0, second=0, microsecond=0)
    conn.execute("DELETE FROM photo_log WHERE at_utc < ?", (utc_iso(clock.now() - timedelta(days=2)),))
    return conn.execute("SELECT count(*) FROM photo_log WHERE person_id = ? AND at_utc >= ?",
                        (person_id, utc_iso(start))).fetchone()[0]


def save(conn: sqlite3.Connection, clock: Clock, where: Path, person_id: int, jpeg: bytes, w: int, h: int) -> int:
    """Store a photo already made clean by `shrink`. Returns its id. Raises Refused."""
    if sent_today(conn, clock, person_id) >= PER_DAY:
        raise Refused("Hoy ya ha mandado muchas fotos. Mañana puede mandar más.")
    where.mkdir(parents=True, exist_ok=True)
    name = secrets.token_hex(16) + ".jpg"
    pid = conn.execute("INSERT INTO photos (person_id, file, width, height, created_at) VALUES (?, ?, ?, ?, ?)",
                       (person_id, name, w, h, utc_iso(clock.now()))).lastrowid
    try:
        (where / name).write_bytes(jpeg)  # the row first: a cleanup finds the file through it
    except OSError:
        conn.execute("DELETE FROM photos WHERE id = ?", (pid,))
        raise Refused("No se ha podido guardar la foto. Pruebe otra vez.") from None
    conn.execute("INSERT INTO photo_log (person_id, at_utc) VALUES (?, ?)", (person_id, utc_iso(clock.now())))
    return pid


def read(conn: sqlite3.Connection, where: Path, person_id: int, photo_id: int) -> bytes | None:
    """The photo, only for the person who sent it."""
    row = conn.execute("SELECT file FROM photos WHERE id = ? AND person_id = ?", (photo_id, person_id)).fetchone()
    if row is None:
        return None
    path = where / row["file"]
    return path.read_bytes() if path.is_file() else None


def drop_unused(conn: sqlite3.Connection, clock: Clock, where: Path, person_id: int | None = None,
                grace: timedelta = timedelta(minutes=10)) -> int:
    """Delete photos no conversation line holds any more (after a purge, a delete or "olvídalo"). A photo just
    saved, whose line is being written by another thread, is left alone for `grace`."""
    sql = ("SELECT id, file FROM photos WHERE id NOT IN (SELECT photo_id FROM chat WHERE photo_id IS NOT NULL) "
           "AND created_at <= ?")
    args: list = [utc_iso(clock.now() - grace)]
    if person_id is not None:
        sql += " AND person_id = ?"
        args.append(person_id)
    rows = conn.execute(sql, args).fetchall()
    for r in rows:
        try:
            (where / r["file"]).unlink(missing_ok=True)
        except OSError:
            continue  # tried again next time
        conn.execute("DELETE FROM photos WHERE id = ?", (r["id"],))
    return len(rows)
