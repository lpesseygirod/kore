"""Lea's controls for the weather and the news, from the command line. No keys needed; both are free GETs.

  place NAME                 look the town up and list the matches
  place NAME --pick N        save match N as the home's place (for the weather)
  place --lat L --lon L --name NAME   save a place by its coordinates
  weather [--day tomorrow]   the facts Pepe would get now
  news [--topic WORDS]       the headlines Pepe would get now
  feeds [SOURCE=URL ...]     show the news feeds, or replace them

--db PATH picks the database (default data/kore.db).
"""

from __future__ import annotations

import argparse
import json
import sys

from kore.info import news, weather
from kore.memory.db import connect
from kore.scheduler.clock import SystemClock


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kore.info")
    ap.add_argument("--db", default="data/kore.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("place")
    p.add_argument("name", nargs="?")
    p.add_argument("--pick", type=int)
    p.add_argument("--lat", type=float)
    p.add_argument("--lon", type=float)
    p = sub.add_parser("weather")
    p.add_argument("--day", choices=["today", "tomorrow", "day_after"], default="today")
    p = sub.add_parser("news")
    p.add_argument("--topic")
    p = sub.add_parser("feeds")
    p.add_argument("feeds", nargs="*", help="SOURCE=URL")
    a = ap.parse_args(argv)

    conn = connect(a.db)
    clock = SystemClock()
    w = weather.Weather(conn, clock, weather.OpenMeteo())
    if a.cmd == "place":
        if a.lat is not None and a.lon is not None:
            if not a.name:
                print("Give a name too: place NAME --lat L --lon L", file=sys.stderr)
                return 2
            w.set_place(weather.Place(a.name, a.lat, a.lon))
            print(f"Saved: {a.name} ({a.lat:.4f}, {a.lon:.4f})")
            return 0
        if not a.name:
            p = w.place()
            print(f"Home place: {p.name}, {p.region} ({p.lat:.4f}, {p.lon:.4f})" if p else "No place saved yet.")
            return 0
        found = weather.OpenMeteo().geocode(a.name)
        if not found:
            print("Nothing found (or no connection).", file=sys.stderr)
            return 1
        if a.pick is None:
            for i, p in enumerate(found, 1):
                print(f"{i}. {p.name}, {p.region} ({p.lat:.4f}, {p.lon:.4f})")
            print("Save one with: place NAME --pick N")
            return 0
        if not 1 <= a.pick <= len(found):
            print(f"--pick must be 1 to {len(found)}", file=sys.stderr)
            return 2
        p = found[a.pick - 1]
        w.set_place(p)
        print(f"Saved: {p.name}, {p.region}")
        return 0
    if a.cmd == "weather":
        print(json.dumps(w.facts({"today": 0, "tomorrow": 1, "day_after": 2}[a.day]), ensure_ascii=False, indent=1))
        return 0
    n = news.News(conn, clock, news.Rss())
    if a.cmd == "news":
        print(json.dumps(n.headlines(a.topic), ensure_ascii=False, indent=1))
        return 0
    if a.feeds:
        feeds = []
        for f in a.feeds:
            source, _, url = f.partition("=")
            if not url.startswith("https://"):
                print(f"Not an https address: {f}", file=sys.stderr)
                return 2
            feeds.append({"source": source, "url": url})
        n.set_feeds(feeds)
    for f in n.feeds():
        print(f"{f['source']}: {f['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
