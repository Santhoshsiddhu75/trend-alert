"""Trend alert: pings your phone when something is spiking on Google search right now.

Reads Google Trends' public "Trending now" feed (no key, no AI) for a few countries, keeps only
the big spikes (search volume over a threshold per country), skips anything already sent, and
pushes each new one to the free ntfy app. Runs every 30 min on GitHub Actions
(.github/workflows/alert.yml), so your PC can be off.

    python trend_alert.py            # check and send
    python trend_alert.py --dry-run  # print what would be sent, send nothing
    python trend_alert.py --test     # send one test notification

Settings: edit THRESHOLDS / MAX_PER_DAY below. The ntfy topic comes from the NTFY_TOPIC
environment variable (a GitHub secret), so it never sits in the code.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Only alert when Google's approximate search volume is at least this much (per country).
THRESHOLDS = {"US": 20000, "GB": 10000, "IN": 20000}
COUNTRY = {"US": "🇺🇸 US", "GB": "🇬🇧 UK", "IN": "🇮🇳 India"}
MAX_PER_DAY = 8  # never more alerts than this in 24 h
FORGET_AFTER_DAYS = 4  # a topic can alert again after this long
STATE = Path(__file__).with_name("seen.json")
NS = {"ht": "https://trends.google.com/trending/rss"}
UA = {"User-Agent": "trend-alert/1.0"}


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
        n = int(re.sub(r"\D", "", traffic) or 0)
        news = it.find("ht:news_item", NS)
        items.append(
            {
                "geo": geo,
                "title": (it.findtext("title") or "").strip(),
                "traffic": n,
                "headline": (news.findtext("ht:news_item_title", "", NS) if news is not None else "").strip(),
                "source": (news.findtext("ht:news_item_source", "", NS) if news is not None else "").strip(),
                "url": (news.findtext("ht:news_item_url", "", NS) if news is not None else "").strip(),
            }
        )
    return items


def push(topic: str, title: str, body: str, click: str = "") -> None:
    headers = {"Title": title.encode("utf-8"), "Tags": "fire", "Priority": "high"}
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
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"seen": {}, "sent": []}
    # Forget old topics and old send times.
    state["seen"] = {k: v for k, v in state["seen"].items() if now - datetime.fromisoformat(v) < timedelta(days=FORGET_AFTER_DAYS)}
    state["sent"] = [t for t in state["sent"] if now - datetime.fromisoformat(t) < timedelta(hours=24)]

    hits = []
    for geo, minimum in THRESHOLDS.items():
        for it in fetch(geo):
            key = it["title"].lower()
            if it["traffic"] >= minimum and key not in state["seen"]:
                hits.append(it)
    hits.sort(key=lambda it: -it["traffic"])

    for it in hits:
        key = it["title"].lower()
        if key in state["seen"]:
            continue  # same topic spiking in two countries: one alert
        if len(state["sent"]) >= MAX_PER_DAY:
            print("daily cap reached")
            break
        title = f"🔥 {it['title']} · {it['traffic']:,}+ searches · {COUNTRY[it['geo']]}"
        body = it["headline"] + (f" ({it['source']})" if it["source"] else "")
        click = it["url"] or "https://www.google.com/search?q=" + urllib.parse.quote(it["title"])
        print(title, "|", body)
        if not dry:
            push(topic, title, body or "Tap to see why it's trending", click)
            state["sent"].append(now.isoformat())
        state["seen"][key] = now.isoformat()

    if not hits:
        print("nothing over the thresholds right now")
    if not dry:
        STATE.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
