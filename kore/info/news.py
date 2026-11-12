"""News headlines on request (BUILD-PLAN 1: "news headlines on request"; 0b.10: outside text).

Headlines come from an RSS feed, are cleaned and kept for 30 minutes, and are given to Claude
as quoted content: the tool that returns them taints the turn, so nothing they say can change
anything (the same rule as family messages). Pepe reads them, never adds to them.

Feed: El País by default; others can be set with `python -m kore.info feeds`. Checked 2026-11-07: RTVE's feeds
stopped updating in 2022; Europa Press sends an incomplete certificate chain, so a secure client
refuses it (and we never switch verification off); El País, El Mundo, 20minutos, eldiario.es and
La Vanguardia all work. Reading headlines aloud at home for the family trial is personal use; a product for sale
needs a licensed news source. One interface, swappable.

Safety of the parsing: size-limited; any document with a DOCTYPE or an entity declaration is
refused by the XML parser itself, whatever its encoding (no XML bombs); every text is unescaped,
stripped of tags and control characters, and cut short; parse() never raises. Fetching runs in a
background thread (kore.info.fetch): a turn waits at most 3 seconds.
"""

from __future__ import annotations

import html
import re
import sqlite3
import xml.etree.ElementTree as ET
from xml.parsers import expat
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from typing import Protocol

from kore.brain.language import fold
from kore.info import http
from kore.info import store as info
from kore.info.fetch import IN_TURN_WAIT_S, Fetcher
from kore.scheduler.clock import Clock

FEEDS = "news_feeds"
CACHE = "news_headlines"
DEFAULT_FEEDS = [{"source": "El País", "url": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada"}]
REFRESH = timedelta(minutes=30)
STALE = timedelta(hours=12)
RECENT = timedelta(hours=36)       # older items aren't "news"
MAX_ITEMS = 30
TITLE_MAX = 200
SUMMARY_MAX = 300
PER_ANSWER = 3


@dataclass
class Headline:
    title: str
    summary: str
    source: str
    published: datetime


class Provider(Protocol):
    def fetch(self, url: str) -> bytes | None: ...


class Rss:
    def fetch(self, url: str) -> bytes | None:
        status, body = http.get(url)
        return body if status == 200 else None


@dataclass
class FakeNews:
    """Tests and scenarios: builds a real RSS document from short items, so the parser is exercised."""
    items: list[dict] = field(default_factory=list)   # {"title", "summary", "published": datetime}
    online: bool = True
    raw: bytes | None = None                         # or exactly these bytes
    calls: int = 0

    def fetch(self, url: str) -> bytes | None:
        self.calls += 1
        if not self.online:
            return None
        if self.raw is not None:
            return self.raw
        body = "".join(f"<item><title>{html.escape(i['title'])}</title><description>{html.escape(i.get('summary', ''))}"
                       f"</description><pubDate>{format_datetime(i['published'])}</pubDate></item>" for i in self.items)
        return f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>{body}</channel></rss>'.encode()


_TAG = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(\s[^<>]*)?/?>")  # real tags only: "menos de <10 grados y >30" stays
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")


def clean(text: str | None, limit: int) -> str:
    t = text or ""
    for _ in range(2):  # some feeds escape twice
        t = html.unescape(t)
    t = _TAG.sub(" ", t)
    t = _CTRL.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s*Leer la noticia completa\s*$", "", t)
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return t


class _Refused(Exception):
    pass


def _no_dtd(data: bytes) -> bool:
    """True if the document declares no DOCTYPE and no entities (checked by the parser, so any encoding)."""
    def refuse(*_):
        raise _Refused()
    p = expat.ParserCreate()
    p.StartDoctypeDeclHandler = refuse
    p.EntityDeclHandler = refuse
    try:
        p.Parse(data, True)
    except _Refused:
        return False
    return True


def parse(data: bytes, source: str) -> list[Headline]:
    """RSS 2.0 or Atom. Never raises: a broken or hostile feed gives no headlines."""
    try:
        return _parse(data, source)
    except Exception:
        return []


def _parse(data: bytes, source: str) -> list[Headline]:
    if not data or not _no_dtd(data):
        return []
    root = ET.fromstring(data)
    out = []
    atom = "{http://www.w3.org/2005/Atom}"
    items = root.findall("./channel/item") or root.findall(f"./{atom}entry")
    for it in items[:MAX_ITEMS]:
        title = clean(it.findtext("title") or it.findtext(f"{atom}title"), TITLE_MAX)
        summary = clean(it.findtext("description") or it.findtext(f"{atom}summary"), SUMMARY_MAX)
        when = _date(it.findtext("pubDate") or it.findtext(f"{atom}updated") or it.findtext(f"{atom}published"))
        if title and when:
            out.append(Headline(title, "" if fold(summary).startswith(fold(title)) else summary, source, when))
    return out


def _date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        d = parsedate_to_datetime(text.strip())
    except (TypeError, ValueError, IndexError):
        try:
            d = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


@dataclass
class News:
    conn: sqlite3.Connection
    clock: Clock
    provider: Provider
    _tried: datetime | None = field(default=None, repr=False)
    _fetcher: Fetcher = field(default_factory=Fetcher, repr=False)

    def feeds(self) -> list[dict]:
        got = info.get(self.conn, FEEDS, self.clock.tz)
        return got[0] if got else DEFAULT_FEEDS

    def set_feeds(self, feeds: list[dict]) -> None:
        info.put(self.conn, FEEDS, feeds, self.clock.now())
        self.conn.execute("DELETE FROM info WHERE key = ?", (CACHE,))
        self._tried = None
        self._fetcher = Fetcher()

    def refresh(self, wait: float = IN_TURN_WAIT_S) -> bool:
        """Fetch the feeds if the headlines are over 30 minutes old, waiting at most `wait` seconds; otherwise
        the fetch carries on in the background and the next call collects it. True if saved now."""
        saved = self._collect(0)
        now = self.clock.now()
        got = info.get(self.conn, CACHE, self.clock.tz)
        due = got is None or not timedelta(0) <= now - got[1] < REFRESH
        if due and not self._fetcher.busy and not (self._tried and timedelta(0) <= now - self._tried < timedelta(minutes=2)):
            self._tried = now
            feeds, provider = self.feeds(), self.provider

            def fetch_all():
                heads = []
                for f in feeds:
                    try:
                        data = provider.fetch(f["url"])
                    except Exception:
                        data = None
                    heads += parse(data, f["source"]) if data else []
                return heads
            self._fetcher.start(fetch_all)
        return self._collect(wait) or saved

    def _collect(self, wait: float) -> bool:
        finished, heads = self._fetcher.collect(wait)
        if not finished or not heads:
            return False
        info.put(self.conn, CACHE, [{"title": h.title, "summary": h.summary, "source": h.source,
                                     "published": h.published.isoformat()} for h in heads], self.clock.now())
        self._tried = None
        return True

    def headlines(self, topic: str | None = None, skip: int = 0, fetch: bool = True) -> dict:
        if fetch:
            self.refresh()
        got = info.get(self.conn, CACHE, self.clock.tz)
        now = self.clock.now()
        if got is None or not (-timedelta(minutes=5) <= now - got[1] <= STALE):
            return {"ok": False, "error": "no_news",
                    "say": "You can't get the news right now (no connection). Say so simply; never make up news."}
        rows = [r for r in got[0] if now - datetime.fromisoformat(r["published"]) <= RECENT]
        seen, fresh = set(), []
        for r in sorted(rows, key=lambda r: datetime.fromisoformat(r["published"]), reverse=True):
            if fold(r["title"]) not in seen:
                seen.add(fold(r["title"]))
                fresh.append(r)
        if topic:
            words = [w for w in re.findall(r"\w+", fold(topic)) if len(w) > 2]
            fresh = [r for r in fresh if all(re.search(rf"\b{re.escape(w)}\b", fold(r["title"] + " " + r["summary"]))
                                             for w in words)]
        if not fresh:
            return {"ok": True, "headlines": [], "say": ("Nothing about that in today's headlines. " if topic else
                    "No recent headlines. ") + "Say so; don't make anything up."}
        page = fresh[skip:skip + PER_ANSWER]
        out = [{"source": r["source"], "title": r["title"], "summary": r["summary"],
                "when": _when(datetime.fromisoformat(r["published"]).astimezone(self.clock.tz), now)} for r in page]
        return {"ok": True, "headlines": out, "more": len(fresh) > skip + PER_ANSWER,
                "say": "Quoted content from the source named: read or summarise faithfully, slowly. Add nothing that "
                       "isn't here (for more detail you only have this summary). No opinions on politics. If there "
                       "are more, offer them (skip: " + str(skip + PER_ANSWER) + ")."}


def _when(t: datetime, now: datetime) -> str:
    if t.date() == now.date():
        return "today " + t.strftime("%H:%M")
    if t.date() == (now - timedelta(days=1)).date():
        return "yesterday " + t.strftime("%H:%M")
    return t.strftime("%Y-%m-%d")
