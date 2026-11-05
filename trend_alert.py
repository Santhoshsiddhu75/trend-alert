"""Trend alert: pings your phone when a PERSON is spiking on Google search right now.

Reads Google Trends' public "Trending now" feed (no key, no AI) for a few countries, keeps only
trends that are about a real person (checked on Wikidata: free, no key), drops game-day noise
(matches, scores, standings, lotteries, prices), skips anything already sent, and pushes each new
one to the free ntfy app. Runs every 30 min on GitHub Actions (.github/workflows/alert.yml), so
your PC can be off.

Why people only (owner, 5 Nov 2026): the channel tells true stories about people, and the first
version mostly pinged football and cricket matches ("aek vs real madrid") and exchange rates.

    python trend_alert.py            # check and send
    python trend_alert.py --dry-run  # print what would be sent, send nothing
    python trend_alert.py --test     # send one test notification
    python trend_alert.py --heartbeat --dry-run  # also show the daily "people trending" digest now

Settings: edit THRESHOLDS / MAX_PER_DAY below. The ntfy topic comes from the NTFY_TOPIC
environment variable (a GitHub secret), so it never sits in the code.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Alert when a person's search volume is at least this much (per country). A death or a scandal
# about someone well known usually passes 10k fast; routine news about them stays in the hundreds.
THRESHOLDS = {"US": 10000, "GB": 5000, "IN": 10000}
# News of a death alerts at half the volume, since those stories move fastest.
DEATH_FACTOR = 0.5
COUNTRY = {"US": "🇺🇸 US", "GB": "🇬🇧 UK", "IN": "🇮🇳 India"}
MAX_PER_DAY = 8  # never more alerts than this in 24 h
HEARTBEAT_UTC = 4 * 60  # daily digest from 04:00 UTC = 9:30 am India time
DIGEST_SIZE = 5  # people in the daily digest
DIGEST_MIN = 200  # ignore people below this volume in the digest
DIGEST_FAME = 25  # …and people on fewer Wikipedias than this (role players' game-day news)
REMEMBER_PEOPLE_DAYS = 7  # cache Wikidata answers, so each topic is looked up once a week
FORGET_AFTER_DAYS = 4  # a topic can alert again after this long
STATE = Path(__file__).with_name("seen.json")
NS = {"ht": "https://trends.google.com/trending/rss"}
UA = {"User-Agent": "trend-alert/2.0 (github.com/Santhoshsiddhu75/trend-alert)"}

# Game-day and everyday noise: a person trending because a match is on isn't a story.
NOISE = re.compile(
    r"\bvs\.?\b|\bv\b|standings|scorecard|\bscore\b|highlights|player stats|game summary|match preview"
    r"|live updates|\blive\b|line-?up|prediction|\bodds\b|injury report|fantasy|box score|ranking"
    r"|powerball|mega millions|lottery|jackpot|horoscope|weather|stock|exchange rate|\bprice\b|sale\b|deals",
    re.I,
)
DEATH = re.compile(r"\bdie[sd]\b|\bdead\b|death|passed away|\bpasses away\b|killed|obituary|tribute", re.I)


def fetch(geo: str) -> list[dict]:
    url = f"https://trends.google.com/trending/rss?geo={geo}"
    for attempt in range(3):
        try:
            data = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()
            break
        except Exception as e:  # network hiccup: try again a little later
            if attempt == 2:
                print(f"{geo}: feed failed ({e})")
                return []
            time.sleep(5)
    items = []
    for it in ET.fromstring(data).iter("item"):
        traffic = it.findtext("ht:approx_traffic", "", NS)
        news = it.findall("ht:news_item", NS)
        first = news[0] if news else None
        items.append(
            {
                "geo": geo,
                "title": (it.findtext("title") or "").strip(),
                "traffic": int(re.sub(r"\D", "", traffic) or 0),
                "headline": (first.findtext("ht:news_item_title", "", NS) if first is not None else "").strip(),
                "headlines": " ".join(n.findtext("ht:news_item_title", "", NS) for n in news),
                "source": (first.findtext("ht:news_item_source", "", NS) if first is not None else "").strip(),
                "url": (first.findtext("ht:news_item_url", "", NS) if first is not None else "").strip(),
            }
        )
    return items


def wikidata(params: dict) -> dict:
    url = "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode({**params, "format": "json"})
    for attempt in range(4):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read())
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise
            time.sleep(int(e.headers.get("Retry-After") or 0) or 5 * (attempt + 1))  # rate limited: wait
    raise RuntimeError("unreachable")


def lookup_person(title: str) -> dict | None:
    """Wikidata's best match for the trend, if it's a human: name, description and how many
    Wikipedias have a page on them (a rough fame score: Ronaldo ~200, a local politician ~3)."""
    words = re.sub(r"[,()]", " ", title).split()
    # "timothee chalamet dune" → also try without the last word; "prince harry, duke of sussex" →
    # also try the part before the comma.
    queries = [" ".join(words), title.split(",")[0].strip(), " ".join(words[:-1]) if len(words) > 2 else ""]
    for query in dict.fromkeys(queries):
        if not query:
            continue
        hits = wikidata({"action": "wbsearchentities", "search": query, "language": "en", "type": "item", "limit": 1})
        if not hits.get("search"):
            continue
        qid = hits["search"][0]["id"]
        shown = hits["search"][0].get("display", {}).get("label", {}).get("value") or hits["search"][0].get("label")
        entity = wikidata({"action": "wbgetentities", "ids": qid, "props": "claims|sitelinks|labels|descriptions", "languages": "en"})["entities"][qid]
        kinds = {c["mainsnak"].get("datavalue", {}).get("value", {}).get("id") for c in entity.get("claims", {}).get("P31", [])}
        if "Q5" not in kinds:  # Q5 = human
            return None
        return {
            "name": entity.get("labels", {}).get("en", {}).get("value") or shown or title.title(),
            "about": entity.get("descriptions", {}).get("en", {}).get("value", ""),
            "wikis": len(entity.get("sitelinks", {})),
        }
    return None


def push(topic: str, title: str, body: str, click: str = "", tags: str = "fire", priority: str = "high") -> None:
    headers = {"Title": title.encode("utf-8"), "Tags": tags, "Priority": priority}
    if click:
        headers["Click"] = click
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"), headers=headers, method="POST")
    urllib.request.urlopen(req, timeout=30).read()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    dry = "--dry-run" in sys.argv
    topic = os.environ.get("NTFY_TOPIC", "")
    if not dry and not topic:
        sys.exit("Set NTFY_TOPIC (the ntfy topic your phone subscribes to).")
    if "--test" in sys.argv:
        push(topic, "✅ Trend alert is working", "You'll get a ping like this when a person spikes on Google.")
        print("test sent")
        return

    now = datetime.now(timezone.utc)
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    state.setdefault("seen", {})
    state.setdefault("sent", [])
    # Forget old topics and old send times.
    state["seen"] = {k: v for k, v in state["seen"].items() if now - datetime.fromisoformat(v) < timedelta(days=FORGET_AFTER_DAYS)}
    state["sent"] = [t for t in state["sent"] if now - datetime.fromisoformat(t) < timedelta(hours=24)]

    today = now.date().isoformat()
    digest_due = (now.hour * 60 + now.minute >= HEARTBEAT_UTC or "--heartbeat" in sys.argv) and state.get("heartbeat") != today

    # Wikidata answers, kept for a week: {topic: {"at": time, "who": person or None}}.
    cache = {
        k: v
        for k, v in state.get("people", {}).items()
        if now - datetime.fromisoformat(v["at"]) < timedelta(days=REMEMBER_PEOPLE_DAYS)
    }
    state["people"] = cache

    def person(it: dict) -> dict | None:
        key = it["title"].lower()
        if key not in cache:
            try:
                who = lookup_person(it["title"])
            except Exception as e:  # Wikidata down: skip for now, try again next run
                print(f"wikidata failed for {it['title']!r}: {e}")
                return None
            cache[key] = {"at": now.isoformat(), "who": who}
            time.sleep(1)  # be gentle with Wikidata
        return cache[key]["who"]

    hits, digest = [], []
    for geo, minimum in THRESHOLDS.items():
        for it in fetch(geo):
            text = f"{it['title']} {it['headlines']}"
            if NOISE.search(it["title"]) or NOISE.search(it["headline"]):
                continue
            it["death"] = bool(DEATH.search(text))
            needed = minimum * (DEATH_FACTOR if it["death"] else 1)
            want_alert = it["traffic"] >= needed and it["title"].lower() not in state["seen"]
            want_digest = digest_due and it["traffic"] >= DIGEST_MIN
            if not (want_alert or want_digest):
                continue
            who = person(it)
            if who is None:
                continue
            it["who"] = who
            if want_alert:
                hits.append(it)
            if want_digest and who["wikis"] >= DIGEST_FAME:
                digest.append(it)
    hits.sort(key=lambda it: -it["traffic"])

    for it in hits:
        key = it["title"].lower()
        if key in state["seen"]:
            continue  # same person spiking in two countries: one alert
        if len(state["sent"]) >= MAX_PER_DAY:
            print("daily cap reached")
            break
        who = it["who"]
        mark = "🕯️" if it["death"] else "🔥"
        title = f"{mark} {who['name']} · {it['traffic']:,}+ searches · {COUNTRY[it['geo']]}"
        lines = [f"{who['about']} · on {who['wikis']} Wikipedias" if who["about"] else f"on {who['wikis']} Wikipedias"]
        if it["headline"]:
            lines.append(it["headline"] + (f" ({it['source']})" if it["source"] else ""))
        click = it["url"] or "https://www.google.com/search?q=" + urllib.parse.quote(it["title"])
        print(title, "|", " | ".join(lines))
        if not dry:
            push(topic, title, "\n".join(lines), click, tags="candle" if it["death"] else "fire")
            state["sent"].append(now.isoformat())
        state["seen"][key] = now.isoformat()

    if not hits:
        print("no person over the thresholds right now")

    # Once a day, a quiet digest of the people trending most, so you know it's running and can spot
    # a rising story early.
    if digest_due:
        top, names = [], set()
        for it in sorted(digest, key=lambda it: -it["traffic"]):
            name = it["who"]["name"]
            if name.lower() in names:
                continue
            names.add(name.lower())
            why = it["headline"][:70] + ("…" if len(it["headline"]) > 70 else "")
            top.append(f"{len(top) + 1}. {name} · {it['traffic']:,}+ · {COUNTRY[it['geo']]}\n   {why}")
            if len(top) == DIGEST_SIZE:
                break
        body = "\n".join(top) if top else "No people trending right now."
        print("digest |", body.replace("\n", " | "))
        if not dry:
            push(topic, "☀️ People trending today", body, "https://trends.google.com/trending?geo=US", tags="sunny", priority="low")
            state["heartbeat"] = today
    if not dry:
        STATE.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
