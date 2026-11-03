# Trend alert

Pings your phone (free **ntfy** app) when something is spiking on Google search, so videos can be
made while a trend is still rising. Runs on GitHub Actions every 30 minutes: free, no AI, no API
keys, your PC can be off.

- Source: Google Trends "Trending now" feed (US, UK, India).
- Alerts only above `THRESHOLDS` in `trend_alert.py` (20,000+ US, 10,000+ UK, 20,000+ India), at
  most `MAX_PER_DAY` (8) a day, never the same topic twice within 4 days.
- The ntfy topic is a repository secret named `NTFY_TOPIC`; the phone subscribes to the same name.
- Run by hand: Actions tab → "Trend alert" → "Run workflow". Test locally:
  `NTFY_TOPIC=<topic> python trend_alert.py --dry-run`.
