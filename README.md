# Trend alert

Pings your phone (free **ntfy** app) when a **person** is spiking on Google search, so videos can be
made while a story is still rising. It runs on GitHub Actions every 30 minutes. It's free, needs no AI
and no API keys, and works with your PC off.

- **Source:** the Google Trends "Trending now" feed for the US, UK and India.
- **People only:** each trend is looked up on Wikidata (free, no key) and kept only if it's a human. Game-day
  and everyday noise is dropped first: matches ("x vs y"), scores, standings, rankings, lotteries,
  prices and weather.
- **Thresholds:** an alert needs `THRESHOLDS` in `trend_alert.py` (10,000+ US, 5,000+ UK, 10,000+ India).
  News of a death needs only half that and comes with a 🕯️. You get at most `MAX_PER_DAY` (8) alerts a
  day, and never the same topic twice within 4 days.
- **What an alert shows:** the person, the search volume and country, who they are, how many Wikipedias
  have a page on them (a rough fame score: Ronaldo ≈ 200), and the headline that explains why.
- **Morning digest:** every morning at 9:30 am India time you get "☀️ People trending today", the top 5
  well-known people (25+ Wikipedias) with one headline each.
- **The ntfy topic:** a repository secret named `NTFY_TOPIC`; the phone subscribes to the same name.
- **Running it yourself:** to run it by hand, go to the Actions tab → "Trend alert" → "Run workflow". To
  test it locally, run `NTFY_TOPIC=<topic> python trend_alert.py --dry-run --heartbeat`.
