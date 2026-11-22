# YYZ Flight Deal Alerts

Daily check of round-trip economy fares from Toronto Pearson (YYZ) for a Christmas
getaway: **out Dec 23 or 24, back Jan 1, 2 or 3**, which makes 6 date combos. It covers
Cancun, Mexico City, Punta Cana, Montego Bay and London (all airports), plus Dublin,
Lisbon, Paris, Rome, Toulouse and Amsterdam on Dec 23 departures only. Every search is
**nonstop only** except Rome and Toulouse (see caveats), so no itinerary connects through the US.

A fare counts as a deal when it is **30% or more below that route's baseline**: the
median of every price we have logged for the same route and date window. The report
also flags **new lows**, meaning the cheapest price seen so far. Christmas fares tend to
climb as the dates get closer, so a 30% drop may never happen.

## How it runs
A scheduled Claude Code session fires every morning and does the following:
1. For each destination and each of the 6 windows in `watchlist.json`, it runs one
   Expedia search with `number_of_stops: 0`, sorted by price, limit 1. Europe uses the
   Dec 23 windows only. That makes 48 searches a day.
2. It writes the results to a JSON file and runs `python check_deals.py <file>`. The
   US-connection filter stays on as a safety net.
3. It commits `data/price_history.csv` and `data/alerts.md`.
4. It sends a push notification with deals, new lows, and today's cheapest fares.

A route stays in **calibration** until it has 7 logged prices. It does not alert
before then.

## Caveats
- Nonstop-only searches miss one-stop itineraries through Canada or Europe, such as
  Air Transat via Montreal.
- A Dec 24 departure to London lands on Christmas morning, when London public transport
  doesn't run.
- The cheapest fare is often basic or ultra-low-cost (Flair, Basic, Light), which
  usually means paid bags and no changes. Read the fare rules before booking.
- Rome and Toulouse have no nonstop from YYZ, so they are searched with up to 1 stop (10
  results) and connections through the US are filtered out. Their prices aren't comparable
  with the nonstop routes, and the cheapest Rome options often connect in Detroit and get dropped.
- Prices are in USD, per adult, as Expedia returns them.
- `data/archive_6wk_2026-10-19.csv` holds the earlier 6-week test run. `xmas` rows in
  `price_history.csv` come from the earlier Dec 21–Jan 4 window, and the checker ignores them.
