"""Trend alert: pings your phone when something is spiking on Google search right now, minus sports.

Reads Google Trends' public "Trending now" feed (no key, no AI) for a few countries, drops sports
(matches, clubs, leagues, tournaments: they top the charts on every game day, however small the game)
and everyday noise (lotteries, horoscopes, prices), skips anything already sent, and pushes each new
spike to the free ntfy app. Any topic counts: a person, a company, a disaster, a film. Each one is
looked up on Wikidata (free, no key) to say what it is and to spot sports. Runs every 30 min on
GitHub Actions (.github/workflows/alert.yml), so your PC can be off.

History (owner): v1 (3 Nov) alerted on anything big, so it was mostly football and cricket. v2
(5 Nov) went people-only. v3 (5 Nov): "it could be anything", just not sports. v4 (6 Nov): no stocks or
markets, UK alerts at the same 10k bar as everywhere else, and nothing under 5k in the daily digest.

    python trend_alert.py            # check and send
    python trend_alert.py --dry-run  # print what would be sent, send nothing
    python trend_alert.py --test     # send one test notification
    python trend_alert.py --heartbeat --dry-run  # also show the daily digest now

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

# Alert when search volume is at least this much (per country). A death, a scandal or a disaster
# usually passes 10k fast; routine news stays in the hundreds.
THRESHOLDS = {"US": 10000, "GB": 10000, "IN": 10000}
# News of a death alerts at half the volume, since those stories move fastest.
DEATH_FACTOR = 0.5
# Athletes trend on every game day: alert for them only at 3× the volume (a record, a scandal).
ATHLETE_FACTOR = 3
COUNTRY = {"US": "🇺🇸 US", "GB": "🇬🇧 UK", "IN": "🇮🇳 India"}
MAX_PER_DAY = 8  # never more alerts than this in 24 h
HEARTBEAT_UTC = 4 * 60  # daily digest from 04:00 UTC = 9:30 am India time
DIGEST_SIZE = 5  # topics in the daily digest
DIGEST_MIN = 5000  # ignore topics below this volume in the digest
REMEMBER_DAYS = 7  # cache Wikidata answers, so each topic is looked up once a week
FORGET_AFTER_DAYS = 4  # a topic can alert again after this long
STATE = Path(__file__).with_name("seen.json")
NS = {"ht": "https://trends.google.com/trending/rss"}
UA = {"User-Agent": "trend-alert/3.0 (github.com/Santhoshsiddhu75/trend-alert)"}

# Sports, in the trend's title or its headline. Catches games even when the trend is just a city
# ("liverpool") or a player.
SPORTS = re.compile(
    r"\bvs\.?\b|\bv\b|standings|scorecard|\bscores?\b|highlights|player (stats|ratings)|game summary"
    r"|\bmatch\b|line-?up|injury report|fantasy|box score|ranking|\bfixtures?\b"
    r"|premier league|champions league|la liga|serie a|bundesliga|europa league|\bfa cup\b|world cup|\bipl\b"
    r"|\bt20\b|\bodi\b|\bnba\b|\bnfl\b|\bnhl\b|\bmlb\b|\bufc\b|\bf1\b|grand prix|\bsquad\b"
    r"|kick-?off|innings|wickets?|\bgoals?\b|touchdown|\bplayoffs?\b|\bcoach\b|transfer|\bmanager\b"
    r"|\bslump\b|\bderby\b|title race|\bfifty\b|\bcentury\b|\bruns\b|\bbowl(ed|er|ing)?\b|\bbatt(ing|er)\b"
    r"|\bday (one|two|three|four|five)\b|\bpenalt(y|ies)\b|\bhat-?trick\b|\bstriker\b|\bquarterback\b",
    re.I,
)
# Athletes, by Wikidata's description ("Indian cricketer", "American basketball player").
ATHLETE = re.compile(
    r"player|cricketer|footballer|athlete|boxer|golfer|tennis|racing driver|coach|wrestler|fighter|jockey"
    r"|swimmer|sprinter|skater|cyclist|gymnast|pitcher|quarterback",
    re.I,
)
# Everyday searches that are never a story.
NOISE = re.compile(
    r"powerball|mega millions|lottery|jackpot|horoscope|weather|stock market|share price|exchange rate"
    r"|\bprice\b|\bsale\b|deals|black friday|\bdownload\b|\blogin\b|live updates|connections|wordle"
    r"|crossword|hints and answers|^rain$|pound value|bank holiday|\bnear me\b",
    re.I,
)
# Stocks and markets, in the title or top headline, or by Wikidata's description. "Shares" isn't here:
# headlines use it for "X shares a photo".
FINANCE = re.compile(
    r"\bftse\b|nasdaq|dow jones|s&p 500|nikkei|sensex|\bnifty\b|\bstocks?\b|stock market|sell-?off"
    r"|market (early )?(call|close|open|report)|wall street|bitcoin|crypto",
    re.I,
)
FINANCE_KIND = re.compile(r"stock (market|exchange)|market index|currency|cryptocurrency", re.I)
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
                "headlines_list": [n.findtext("ht:news_item_title", "", NS) for n in news],
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


def lookup(title: str) -> dict | None:
    """Wikidata's best match for the trend: name, what it is, whether it's a person or sports-related,
    and how many Wikipedias have a page on it (a rough fame score: Ronaldo ~200, a local politician
    ~3). None when Wikidata has nothing."""
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
        claims = entity.get("claims", {})
        kinds = {c["mainsnak"].get("datavalue", {}).get("value", {}).get("id") for c in claims.get("P31", [])}
        return {
            "name": entity.get("labels", {}).get("en", {}).get("value") or shown or title.title(),
            "about": entity.get("descriptions", {}).get("en", {}).get("value", ""),
            "wikis": len(entity.get("sitelinks", {})),
            "human": "Q5" in kinds,  # Q5 = human
            "sport": "P641" in claims,  # P641 = sport: clubs, leagues, tournaments, athletes
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
        push(topic, "✅ Trend alert is working", "You'll get a ping like this when something big spikes on Google.")
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

    # Wikidata answers, kept for a week: {topic: {"at": time, "who": answer or None}}. Answers saved
    # by v2 (people only, no "sport" field) are dropped and looked up again.
    cache = {
        k: v
        for k, v in state.get("wikidata", {}).items()
        if now - datetime.fromisoformat(v["at"]) < timedelta(days=REMEMBER_DAYS)
    }
    state.pop("people", None)
    state["wikidata"] = cache

    def identify(it: dict) -> tuple[bool, dict | None]:
        """(looked up OK, Wikidata's answer or None)."""
        key = it["title"].lower()
        if key not in cache:
            try:
                who = lookup(it["title"])
            except Exception as e:  # Wikidata down: skip for now, try again next run
                print(f"wikidata failed for {it['title']!r}: {e}")
                return False, None
            cache[key] = {"at": now.isoformat(), "who": who}
            time.sleep(1)  # be gentle with Wikidata
        return True, cache[key]["who"]

    hits, digest = [], []
    for geo, minimum in THRESHOLDS.items():
        for it in fetch(geo):
            if NOISE.search(it["title"]) or NOISE.search(it["headline"]):
                continue
            if FINANCE.search(it["title"]) or FINANCE.search(it["headline"]):
                continue
            # Sports: in the title, the top headline, or two of the trend's headlines.
            sporty = sum(bool(SPORTS.search(h)) for h in it["headlines_list"])
            if SPORTS.search(it["title"]) or SPORTS.search(it["headline"]) or sporty >= 2:
                continue
            it["death"] = bool(DEATH.search(f"{it['title']} {it['headlines']}"))
            needed = minimum * (DEATH_FACTOR if it["death"] else 1)
            want_alert = it["traffic"] >= needed and it["title"].lower() not in state["seen"]
            want_digest = digest_due and it["traffic"] >= DIGEST_MIN
            if not (want_alert or want_digest):
                continue
            ok, who = identify(it)
            if not ok:
                continue
            if who and who["sport"] and not who["human"]:
                continue  # a club, a league, a tournament
            if who and FINANCE_KIND.search(who["about"]):
                continue  # a stock index, a currency
            athlete = bool(who and who["human"] and ATHLETE.search(who["about"]))
            if athlete and not it["death"]:
                want_digest = False  # athletes are in the news every game day
                if it["traffic"] < needed * ATHLETE_FACTOR:
                    want_alert = False
            it["who"] = who
            if want_alert:
                hits.append(it)
            if want_digest:
                digest.append(it)
    hits.sort(key=lambda it: -it["traffic"])

    def describe(it: dict) -> tuple[str, str]:
        who = it["who"]
        name = who["name"] if who else it["title"].title()
        about = f"{who['about']} · on {who['wikis']} Wikipedias" if who and who["about"] else ""
        return name, about

    for it in hits:
        key = it["title"].lower()
        if key in state["seen"]:
            continue  # same topic spiking in two countries: one alert
        if len(state["sent"]) >= MAX_PER_DAY:
            print("daily cap reached")
            break
        name, about = describe(it)
        mark = "🕯️" if it["death"] else "🔥"
        title = f"{mark} {name} · {it['traffic']:,}+ searches · {COUNTRY[it['geo']]}"
        headline = it["headline"] + (f" ({it['source']})" if it["source"] else "")
        lines = [line for line in (about, headline) if line]
        click = it["url"] or "https://www.google.com/search?q=" + urllib.parse.quote(it["title"])
        print(title, "|", " | ".join(lines))
        if not dry:
            push(topic, title, "\n".join(lines) or "Tap to see why it's trending", click, tags="candle" if it["death"] else "fire")
            state["sent"].append(now.isoformat())
        state["seen"][key] = now.isoformat()

    if not hits:
        print("nothing over the thresholds right now")

    # Once a day, a quiet digest of the biggest non-sports trends, so you know it's running and can
    # spot a rising story early.
    if digest_due:
        top, names = [], set()
        for it in sorted(digest, key=lambda it: -it["traffic"]):
            name, _ = describe(it)
            if name.lower() in names:
                continue
            names.add(name.lower())
            why = it["headline"][:70] + ("…" if len(it["headline"]) > 70 else "")
            top.append(f"{len(top) + 1}. {name} · {it['traffic']:,}+ · {COUNTRY[it['geo']]}\n   {why}")
            if len(top) == DIGEST_SIZE:
                break
        body = "\n".join(top) if top else "Nothing big trending right now."
        print("digest |", body.replace("\n", " | "))
        if not dry:
            push(topic, "☀️ Trending today (no sports)", body, "https://trends.google.com/trending?geo=US", tags="sunny", priority="low")
            state["heartbeat"] = today
    if not dry:
        STATE.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
