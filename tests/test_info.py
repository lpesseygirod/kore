"""Weather and news (phase 6c), offline: fake providers, a fake clock."""

import threading
from datetime import datetime, timedelta

from kore.scheduler.clock import HOME_TZ

import pytest

from kore.brain import tools
from kore.brain.clients import ScriptedClient, text
from kore.brain.conversation import Brain
from kore.brain.session import Person, Session
from kore.info import Info, news, weather
from kore.info.__main__ import main as info_cli
from kore.manners.gate import SAY, Item
from kore.scheduler.engine import Event

PLACE = weather.Place("Villanueva", 40.4, -3.7, "Madrid")
RAIN_TOMORROW = {"today": {"code": 1, "max": 17, "min": 6, "rain": 5, "wind": 12},
                 "tomorrow": {"code": 63, "max": 12, "min": 7, "rain": 90, "wind": 35,
                              "hours": {"10": {"code": 0, "rain": 0, "wind": 5, "temp": 9}}},
                 "day_after": {"code": 3, "max": 31, "min": 18}}


@pytest.fixture
def fake(clock):
    return weather.FakeWeather(weather.fake_forecast(clock.now(), RAIN_TOMORROW))


@pytest.fixture
def w(conn, clock, fake):
    out = weather.Weather(conn, clock, fake)
    out.set_place(PLACE)
    return out


# --- weather ---------------------------------------------------------------------------------


def test_no_place_means_no_guess(conn, clock, fake):
    r = weather.Weather(conn, clock, fake).facts(0)
    assert r["error"] == "no_place" and "Don't guess" in r["say"] and fake.calls == 0


def test_today_and_tomorrow_in_spanish_words(w):
    today = w.facts(0)
    assert today["ok"] and today["place"] == "Villanueva" and today["sky"] == "casi despejado"
    assert today["max_c"] == 17 and "now" in today and "forecast_from" not in today
    tomorrow = w.facts(1)
    assert tomorrow["sky"] == "lluvia" and tomorrow["rain_chance_pct"] == 90 and "now" not in tomorrow
    assert len(tomorrow["hours"]) == 8  # a whole day: every 3 hours


def test_a_window_of_hours(w):
    """The grandfather's "mañana de 10 a 14": each hour, the sun at 10 shows."""
    r = w.facts(1, 10, 14)
    assert [h["hour"] for h in r["hours"]] == [10, 11, 12, 13, 14]
    assert r["hours"][0] == {"hour": 10, "temp_c": 9, "sky": "despejado", "rain_chance_pct": 0, "wind_kmh": 5}


def test_fetched_at_most_every_30_minutes(w, fake, clock):
    w.facts(0)
    w.facts(1)
    assert fake.calls == 1
    clock.advance(minutes=31)
    w.facts(0)
    assert fake.calls == 2


def test_offline_uses_the_last_forecast_and_says_when_it_was_made(w, fake, clock):
    w.facts(0)
    fake.online = False
    clock.advance(hours=3)
    r = w.facts(0)
    assert r["ok"] and r["forecast_from"] == "today at " + (clock.now() - timedelta(hours=3)).strftime("%H:%M")
    assert "según la previsión" in r["say"] and "now" not in r  # a 3-hour-old reading isn't "now"
    clock.advance(hours=10)  # 13 hours old: not used
    assert w.facts(0)["error"] == "no_forecast"


def test_a_dead_connection_is_not_retried_every_turn(w, fake, clock):
    fake.online = False
    assert w.facts(0)["error"] == "no_forecast"
    w.facts(0)
    assert fake.calls == 1  # the second turn didn't wait again
    clock.advance(minutes=3)
    w.facts(0)
    assert fake.calls == 2


def test_clock_jumping_back_doesnt_serve_a_future_forecast(w, fake, clock):
    w.facts(0)
    fake.online = False
    clock.set(clock.now() - timedelta(hours=2))
    assert w.facts(0)["error"] == "no_forecast"


@pytest.mark.parametrize("bad", [None, {}, {"daily": {"time": ["2026-11-09"]}}, "not json",
                                 {"daily": {"time": [], "weather_code": []}}])
def test_broken_forecasts_are_not_saved(conn, clock, bad):
    w = weather.Weather(conn, clock, weather.FakeWeather(bad))
    w.set_place(PLACE)
    assert w.facts(0)["error"] == "no_forecast"


def test_a_provider_that_crashes_is_no_connection(conn, clock):
    class Boom:
        name = "boom"

        def forecast(self, place):
            raise RuntimeError("x")
    w = weather.Weather(conn, clock, Boom())
    w.set_place(PLACE)
    assert w.facts(0)["error"] == "no_forecast"


def test_beyond_what_the_forecast_covers(w, clock):
    clock.advance(days=2)  # the provider still sends the old forecast: the day after next isn't in it
    assert w.facts(2)["error"] == "beyond_forecast"


def test_hot_day(conn, clock):
    hot = weather.FakeWeather(weather.fake_forecast(clock.now(), {"today": {"max": 34}}))
    w = weather.Weather(conn, clock, hot)
    w.set_place(PLACE)
    assert not w.hot_today()  # cache only: nothing fetched yet
    w.refresh()
    assert w.hot_today()


def test_a_new_place_drops_the_old_forecast(w, fake):
    w.facts(0)
    w.set_place(weather.Place("Otro", 41.0, -4.0))
    w.facts(0)
    assert fake.calls == 2


# --- news ------------------------------------------------------------------------------------


def items(clock, *titles, hours_ago=1, summary="Resumen breve."):
    return [{"title": t, "summary": summary, "published": clock.now() - timedelta(hours=hours_ago)} for t in titles]


@pytest.fixture
def feed(clock):
    return news.FakeNews(items(clock, "Llueve en Madrid", "El Real Madrid gana", "Sube el pan", "Nueva ley",
                               "Fiestas del pueblo"))


@pytest.fixture
def n(conn, clock, feed):
    return news.News(conn, clock, feed)


def test_three_headlines_at_a_time_then_more(n):
    first = n.headlines()
    assert [h["title"] for h in first["headlines"]] == ["Llueve en Madrid", "El Real Madrid gana", "Sube el pan"]
    assert first["more"] and "skip: 3" in first["say"] and "Add nothing" in first["say"]
    rest = n.headlines(skip=3)
    assert [h["title"] for h in rest["headlines"]] == ["Nueva ley", "Fiestas del pueblo"] and not rest["more"]
    assert rest["headlines"][0]["source"] == "El País"


def test_a_topic(n):
    assert [h["title"] for h in n.headlines("el real madrid")["headlines"]] == ["El Real Madrid gana"]
    r = n.headlines("baloncesto")
    assert r["headlines"] == [] and "Nothing about that" in r["say"]


def test_old_items_are_not_news(conn, clock):
    """RTVE's feed still serves 2022 stories: never read them as today's news."""
    n = news.News(conn, clock, news.FakeNews(items(clock, "Noticia de 2022", hours_ago=24 * 400)))
    r = n.headlines()
    assert r["headlines"] == [] and "No recent headlines" in r["say"]


def test_offline_with_no_cache_never_invents(conn, clock):
    r = news.News(conn, clock, news.FakeNews(online=False)).headlines()
    assert r["error"] == "no_news" and "never make up news" in r["say"]


def test_offline_uses_the_last_headlines(n, feed, clock):
    n.headlines()
    feed.online = False
    clock.advance(hours=2)
    assert n.headlines()["headlines"]
    clock.advance(hours=11)
    assert n.headlines()["error"] == "no_news"


def test_cleaning():
    raw = ("<?xml version='1.0'?><rss><channel><item><title>Se&amp;amp;ntilde;ores &lt;b&gt;al d‮ía&lt;/b&gt;"
           "</title><description>&lt;ul&gt;&lt;li&gt;Uno&lt;/li&gt;&lt;/ul&gt;&lt;a href='x'&gt;Leer la noticia "
           "completa&lt;/a&gt;</description><pubDate>Sat, 07 Nov 2026 11:54:06 GMT</pubDate></item></channel></rss>")
    [h] = news.parse(raw.encode(), "S")
    assert h.title == "Señores al día" and h.summary == "Uno"


def test_long_text_is_cut_at_a_word():
    t = news.clean("palabra " * 100, 50)
    assert len(t) <= 51 and t.endswith("…") and not t.endswith(" …")


@pytest.mark.parametrize("raw", [
    b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><rss><channel><item>'
    b'<title>&b;</title><pubDate>Sat, 07 Nov 2026 11:54:06 GMT</pubDate></item></channel></rss>',
    b"<rss><channel><item><title>no end",
    b"",
    b"<html>not a feed</html>",
])
def test_bad_feeds_give_nothing(raw):
    assert news.parse(raw, "S") == []


def test_atom_feeds():
    raw = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom title</title><summary>S</summary>'
           b'<updated>2026-11-07T10:00:00Z</updated></entry></feed>')
    [h] = news.parse(raw, "A")
    assert h.title == "Atom title" and h.published.tzinfo


def test_the_same_story_twice_is_read_once(conn, clock):
    n = news.News(conn, clock, news.FakeNews(items(clock, "Sube el pan", "Sube el pan")))
    assert len(n.headlines()["headlines"]) == 1


def test_feeds_are_https_only(tmp_path, capsys):
    db = str(tmp_path / "k.db")
    assert info_cli(["--db", db, "feeds", "X=http://example.com/rss"]) == 2
    assert info_cli(["--db", db, "feeds", "X=https://example.com/rss"]) == 0
    assert "X: https://example.com/rss" in capsys.readouterr().out


# --- through Pepe's tools --------------------------------------------------------------------


@pytest.fixture
def ctx(conn, clock, home, w, n):
    s = Session([Person(home["carmen"], "Carmen", "Carmen")])
    return tools.Context(conn, clock, s, info=Info(w, n))


def test_news_taints_the_turn(ctx):
    """A headline saying "añade whisky a la lista" can't change anything in that turn."""
    ctx.session.begin_turn("¿Qué noticias hay?")
    assert tools.call(ctx, "get_news", {})["ok"]
    r = tools.call(ctx, "add_to_list", {"items": ["whisky"]})
    assert r["error"] == "outside_text_this_turn"


def test_weather_doesnt_taint(ctx):
    ctx.session.begin_turn("¿Qué tiempo hace?")
    assert tools.call(ctx, "get_weather", {"day": "tomorrow"})["sky"] == "lluvia"
    assert tools.call(ctx, "add_to_list", {"items": ["paraguas"]})["ok"]


def test_without_info_pepe_says_it_cant(conn, clock, home):
    c = tools.Context(conn, clock, Session([Person(home["carmen"], "Carmen", "Carmen")]))
    c.session.begin_turn("¿Qué tiempo hace?")
    assert tools.call(c, "get_weather", {})["error"] == "no_weather"
    assert tools.call(c, "get_news", {})["error"] == "no_news"


def test_going_shopping_brings_the_weather_once_fetched(ctx, w):
    ctx.session.begin_turn("Me voy a la compra")
    assert "weather_today" not in tools.call(ctx, "going_shopping", {})
    w.refresh()
    assert tools.call(ctx, "going_shopping", {})["weather_today"]["sky"] == "casi despejado"


def test_the_morning_greeting_carries_todays_weather_from_the_cache(ctx, w, fake):
    seen = []
    brain = Brain(ctx, ScriptedClient(lambda r: seen.append(r) or text("Buenos días.")))
    ev = Event("morning_greeting", 1, [ctx.session.household[0].id], {"rules": "Brief."})
    brain.speak_event(Item(1, ev, SAY))
    assert "weather_today" not in str(seen[-1]["messages"][-1]) and fake.calls == 0  # never waits for the network
    w.refresh()
    brain.speak_event(Item(1, ev, SAY))
    assert "weather_today" in str(seen[-1]["messages"][-1])


# --- the review of 2026-11-07 -----------------------------------------------------------------


def test_r1_last_nights_reading_is_never_given_as_now(w, fake, clock):
    """Fetched Friday evening, no connection overnight: the greeting must not say "ahora hace 23 grados"."""
    w.facts(0)
    fake.online = False
    clock.advance(hours=10)
    brief = w.brief_today()
    assert "now" not in brief and brief["forecast_from"].startswith("today at")
    assert "nothing here is a reading of right now" in brief["say"]


def test_r1_yesterdays_forecast_says_yesterday(conn):
    from kore.scheduler.clock import FakeClock
    clock = FakeClock(datetime(2026, 11, 6, 20, 0, tzinfo=HOME_TZ))
    fake = weather.FakeWeather(weather.fake_forecast(clock.now(), RAIN_TOMORROW))
    w = weather.Weather(conn, clock, fake)
    w.set_place(PLACE)
    w.refresh()
    fake.online = False
    clock.set(datetime(2026, 11, 7, 7, 30, tzinfo=HOME_TZ))
    w.refresh()
    brief = w.brief_today()
    assert brief["forecast_from"] == "yesterday at 20:00" and "now" not in brief


def test_r2_rain_between_samples_shows_in_its_block(conn, clock):
    days = {"tomorrow": {"code": 1, "rain": 0, "hours": {"16": {"code": 61, "rain": 70}, "17": {"code": 63, "rain": 90}}}}
    w = weather.Weather(conn, clock, weather.FakeWeather(weather.fake_forecast(clock.now(), days)))
    w.set_place(PLACE)
    r = w.facts(1, 12, 20)
    afternoon = [h for h in r["hours"] if h["hour"] <= 16 < h["until"] or h["hour"] <= 17 < h["until"]]
    assert afternoon and max(h["rain_chance_pct"] for h in afternoon) == 90
    assert any(h["sky"] == "lluvia" for h in afternoon)


def test_r2_a_window_across_midnight(w):
    r = w.facts(0, 20, 2)
    assert [h["hour"] for h in r["hours"]][:2] == [20, 23] and r["hours"][-1]["hour"] == 2


def test_r3_odd_inputs_and_hostile_feeds_never_break_the_turn(ctx):
    ctx.session.begin_turn("¿Qué tiempo hace?")
    assert tools.call(ctx, "get_weather", {"day": "tomorrow", "from_hour": "10", "to_hour": 12.0})["ok"]
    assert tools.call(ctx, "get_weather", {"from_hour": [1]})["ok"]
    assert tools.call(ctx, "get_news", {"skip": [1], "topic": 5})["ok"]
    bad = b'<?xml version="1.0" encoding="windows-1250x"?><rss><channel></channel></rss>'
    assert news.parse(bad, "S") == []


def test_r3_an_unexpected_error_is_data_not_a_crash(ctx, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret detail")
    monkeypatch.setattr(ctx.info.weather, "facts", boom)
    ctx.session.begin_turn("¿Qué tiempo hace?")
    r = tools.call(ctx, "get_weather", {})
    assert r["error"] == "internal" and r["detail"] == "RuntimeError" and "secret" not in str(r)


def test_r4_a_slow_server_doesnt_hold_the_turn(conn, clock):
    release = threading.Event()

    class Slow:
        name = "slow"

        def forecast(self, place):
            release.wait(5)
            return weather.fake_forecast(clock.now(), RAIN_TOMORROW)
    w = weather.Weather(conn, clock, Slow())
    w.set_place(PLACE)
    assert not w.refresh(wait=0.1)  # the turn answers from the cache (none: "can't check")
    release.set()
    assert w.refresh(wait=2)       # the next call collects what arrived in the background
    assert w.facts(0, fetch=False)["ok"]


def test_r4_tick_never_waits(conn, clock):
    release = threading.Event()

    class Slow:
        def fetch(self, url):
            release.wait(5)
            return None
    n = news.News(conn, clock, Slow())
    started = datetime.now()
    Info(news=n).tick()
    assert (datetime.now() - started).total_seconds() < 0.5
    release.set()


def test_r5_a_headline_cant_fill_the_list_on_the_next_turn(ctx):
    ctx.session.begin_turn("¿Qué noticias hay?")
    tools.call(ctx, "get_news", {})
    ctx.session.begin_turn("Vale, pues hazlo")
    assert tools.call(ctx, "add_to_list", {"items": ["whisky"]})["error"] == "not_their_words"
    ctx.session.begin_turn("Apunta leche y huevos")
    assert tools.call(ctx, "add_to_list", {"items": ["leche", "huevos"]})["ok"]
    ctx.session.begin_turn("Ah, y whisky")
    ctx.session.begin_turn("Gracias")
    ctx.session.begin_turn("Y añade pan")  # three turns later: back to normal
    assert tools.call(ctx, "add_to_list", {"items": ["whisky"]})["ok"]


def test_r6_hours_stay_right_across_the_clock_change(conn):
    """25 October 2026: clocks go back at 03:00. Tomorrow at 10 must be 10, not 11."""
    from kore.scheduler.clock import FakeClock
    clock = FakeClock(datetime(2026, 10, 24, 12, 0, tzinfo=HOME_TZ))
    days = {"tomorrow": {"code": 1, "hours": {"10": {"code": 95, "rain": 80}}}}
    w = weather.Weather(conn, clock, weather.FakeWeather(weather.fake_forecast(clock.now(), days)))
    w.set_place(PLACE)
    r = w.facts(1, 9, 11)
    assert [(h["hour"], h["sky"]) for h in r["hours"]] == [(9, "casi despejado"), (10, "tormenta"),
                                                           (11, "casi despejado")]
    assert len([h for h in w.facts(1, 0, 4)["hours"]]) == 6  # 02:00 happens twice that night


def test_r7_the_day_after_tomorrow_just_after_midnight(conn):
    from kore.scheduler.clock import FakeClock
    clock = FakeClock(datetime(2026, 11, 9, 23, 50, tzinfo=HOME_TZ))
    w = weather.Weather(conn, clock, weather.FakeWeather(weather.fake_forecast(clock.now(), RAIN_TOMORROW)))
    w.set_place(PLACE)
    w.refresh()
    clock.set(datetime(2026, 11, 10, 0, 10, tzinfo=HOME_TZ))
    assert w.facts(2)["ok"]


def test_r8_a_utf16_entity_bomb_is_refused():
    doc = ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel><item>'
           '<title>&a;</title><pubDate>Sat, 07 Nov 2026 11:54:06 GMT</pubDate></item></channel></rss>')
    assert news.parse(doc.encode("utf-16"), "S") == []


def test_minor_text_and_order(conn, clock):
    assert news.clean("menos de <10 grados y >30 <b>hoy</b>", 100) == "menos de <10 grados y >30 hoy"
    n = news.News(conn, clock, news.FakeNews(items(clock, "Un disparo en el centro")))
    assert n.headlines("paro")["headlines"] == []
    raw = (b"<rss><channel>"
           b"<item><title>Earlier</title><pubDate>Mon, 09 Nov 2026 05:30:00 +0100</pubDate></item>"
           b"<item><title>Later</title><pubDate>Mon, 09 Nov 2026 01:00:00 -0500</pubDate></item>"
           b"</channel></rss>")  # as text "05:30+01:00" sorts after "01:00-05:00"; in time it is earlier
    n = news.News(conn, clock, news.FakeNews(raw=raw))
    n.set_feeds([{"source": "S", "url": "https://x"}])
    assert [h["title"] for h in n.headlines()["headlines"]] == ["Later", "Earlier"]


def test_minor_no_https_to_http_redirect():
    from urllib.request import Request
    from kore.info.http import _HttpsOnly
    assert _HttpsOnly().redirect_request(Request("https://a.es/rss"), None, 302, "", {}, "http://a.es/rss") is None


def test_today_means_the_rest_of_today(conn, clock):
    """Seen live: at 17:00 "today" was "lluvia débil" because of rain at 3 am."""
    clock.set(clock.now().replace(hour=17, minute=0))
    days = {"today": {"code": 61, "rain": 55, "hours": {str(h): {"code": 61 if h < 6 else 0, "rain": 55 if h < 6 else 0}
                                                         for h in range(24)}}}
    w = weather.Weather(conn, clock, weather.FakeWeather(weather.fake_forecast(clock.now(), days)))
    w.set_place(PLACE)
    r = w.facts(0)
    assert r["hours"][0]["hour"] == 17 and r["rest_of_today"] == {"sky": "despejado", "rain_chance_pct": 0}
    assert w.brief_today()["rest_of_today"]["sky"] == "despejado"
