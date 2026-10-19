# YYZ Flight Deal Alerts

Daily check of round-trip economy fares from Toronto Pearson (YYZ) for the Christmas
period (Dec 21, 2026 – Jan 4, 2027). It covers Cancun, Mexico City, the UK and Europe,
and **never uses itineraries that connect through the US**.

A fare counts as a deal when it is **30% or more below that route's baseline**: the
median of every price we have logged for the same route and date window. The report
also flags **new lows**, meaning the cheapest price seen so far. Christmas fares tend to
climb as the dates get closer, so a 30% drop may never happen.

## How it runs
A scheduled Claude Code session fires every morning and does the following:
1. For each destination and window in `watchlist.json`, it runs two Expedia searches:
   - a general search sorted by price (`results_per_search` results)
   - a nonstop-only search (`number_of_stops: 0`, 2 results). Expedia's cheapest
     Christmas fares mostly connect through US hubs, so without this search many
     routes would have no eligible option at all.
   Priority destinations (CUN, MEX, London) also get the ±2-day windows.
2. It writes every option, with its connection airports, to a JSON file and runs
   `python check_deals.py <file>`. The script drops anything that connects via a US
   airport and logs the cheapest remaining fare.
3. It commits `data/price_history.csv` and `data/alerts.md`.
4. It sends a push notification with deals, new lows, and today's cheapest fares.

A route stays in **calibration** until it has 7 logged prices. It does not alert
before then.

## Caveats
- Excluding US connections costs real money. On day one the cheapest non-US fare was
  $150–$830 more expensive than the cheapest fare overall, depending on the route.
- The cheapest fare is often basic or ultra-low-cost (Flair, Basic, Light), which
  usually means paid bags and no changes. Read the fare rules before booking.
- Prices are in USD, per adult, as Expedia returns them.
- `data/archive_6wk_2026-10-19.csv` holds the earlier, unrelated 6-week test run.
