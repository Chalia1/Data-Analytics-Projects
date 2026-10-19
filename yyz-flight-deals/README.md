# YYZ Flight Deal Alerts

Daily check of round-trip economy fares from Toronto Pearson (YYZ) to the routes in
`watchlist.json`. A fare counts as a deal when it is **30% or more below that route's
baseline**: the median of every price we have logged for the same route and booking
window.

## How it runs
A scheduled Claude Code session fires every morning and does the following:
1. For each destination and each window in `watchlist.json` (departing today+45 and
   today+90 days, 7-night trip), it searches Expedia and takes the cheapest fare.
2. It writes those fares to a JSON file and runs `python check_deals.py <file>`.
3. It commits the updated `data/price_history.csv` and `data/alerts.md`.
4. It sends a push notification with any deals it found.

A route stays in **calibration** until it has 7 logged prices. It does not alert
before then.

## Caveats
- The cheapest fare is often basic or ultra-low-cost (Flair, basic economy), which
  usually means paid bags and no changes. Read the fare rules before booking.
- The search covers only the routes in the watchlist, not every destination.
  To change coverage, add or remove entries there.
- Prices are in USD, as Expedia returns them.
