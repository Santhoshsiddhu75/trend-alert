# Trend alert

Pings your phone (free **ntfy** app) when something is spiking on Google search, **except sports**, so
videos can be made while a story is still rising. It runs on GitHub Actions every 30 minutes. It's free,
needs no AI and no API keys, and works with your PC off.

- **Source:** the Google Trends "Trending now" feed for the US, UK and India.
- **Any topic counts:** a person, a company, a disaster, a film. Each trend is looked up on Wikidata (free,
  no key) to say what it is.
- **No sports:** football and cricket top the charts on every game day, however small the match, so these
  are dropped:
  - matches, scores, standings and rankings;
  - clubs, leagues and tournaments (Wikidata tags them with a sport);
  - anything whose headlines read like a game.

  Athletes only alert at 3× the volume (a record, a scandal) or when they die.
- **No everyday noise:** lotteries, horoscopes, puzzles, weather, prices and sales.
- **Thresholds:** an alert needs `THRESHOLDS` in `trend_alert.py` (10,000+ US, 5,000+ UK, 10,000+ India).
  News of a death needs only half that and comes with a 🕯️. You get at most `MAX_PER_DAY` (8) alerts a
  day, and never the same topic twice within 4 days.
- **What an alert shows:** the topic, the search volume and country, what it is, how many Wikipedias have
  a page on it (a rough fame score: Ronaldo ≈ 200), and the headline that explains why.
- **Morning digest:** every morning at 9:30 am India time you get "☀️ Trending today (no sports)", the top 5
  trends with one headline each.
- **The ntfy topic:** a repository secret named `NTFY_TOPIC`; the phone subscribes to the same name.
- **Running it yourself:** to run it by hand, go to the Actions tab → "Trend alert" → "Run workflow". To
  test it locally, run `NTFY_TOPIC=<topic> python trend_alert.py --dry-run --heartbeat`.
