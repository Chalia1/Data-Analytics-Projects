"""Log today's YYZ fare observations and flag deals >= threshold below baseline.

Usage:
    python check_deals.py observations.json

observations.json is a list, one entry per search:
    {"dest": "LIS", "window": "xmas", "depart": "2026-12-21", "return": "2027-01-04",
     "options": [{"price_usd": 524.0, "airline": "TAP Portugal", "via": ["OPO"]}, ...]}

"via" lists every connection airport across both directions. When exclude_us is set,
options connecting through a US airport are dropped and the cheapest remaining one
is logged.

The baseline for a route+window is the median of prior observed prices. Routes
with fewer than min_history_for_baseline prior observations are still calibrating
and never alert -- guessed baselines produced mostly false positives.
"""
import csv
import json
import statistics
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
HISTORY = ROOT / "data" / "price_history.csv"
ALERTS = ROOT / "data" / "alerts.md"
FIELDS = ["date", "dest", "window", "depart", "return", "price_usd", "airline", "via"]

# US airports that show up as connections on YYZ itineraries.
US_AIRPORTS = set("""
ATL BOS BWI CLT CVG DCA DEN DFW DTW EWR FLL HNL IAD IAH JFK LAS LAX LGA MCO MDW MIA
MSP ORD PHL PHX PIT RDU SAN SEA SFO SLC STL TPA AUS BNA BDL BUF CLE CMH IND JAX MCI
MEM MKE MSY OAK PDX RSW SAT SJC SJU SMF ABQ ANC OMA OKC TUL RIC ORF ALB SYR ROC
PBI SRQ DAL HOU ONT BUR SNA LGB ELP BOI GEG
""".split())


def load_history():
    if not HISTORY.exists():
        return []
    with HISTORY.open() as f:
        return list(csv.DictReader(f))


def prices_for(history, dest, window):
    return [float(r["price_usd"]) for r in history if r["dest"] == dest and r["window"] == window]


def pick_option(options, exclude_us):
    eligible = [o for o in options if not (exclude_us and US_AIRPORTS & set(o.get("via", [])))]
    return min(eligible, key=lambda o: o["price_usd"], default=None)


def main(obs_path):
    cfg = json.loads((ROOT / "watchlist.json").read_text())
    dests = {d["code"]: d for d in cfg["destinations"]}
    threshold = cfg["deal_threshold"]
    min_n = cfg["min_history_for_baseline"]
    history = load_history()
    today = date.today().isoformat()
    observations = json.loads(Path(obs_path).read_text())

    logged, deals, new_lows, no_option, calibrating = [], [], [], [], 0
    for o in observations:
        dest = dests.get(o["dest"])
        if dest is None:
            continue
        best = pick_option(o.get("options", []), cfg.get("exclude_us", False))
        if best is None:
            no_option.append(f"{o['dest']}/{o['window']}")
            continue
        row = {"date": today, "dest": o["dest"], "window": o["window"], "depart": o["depart"],
               "return": o["return"], "price_usd": best["price_usd"], "airline": best.get("airline", ""),
               "via": " ".join(best.get("via", [])) or "nonstop"}
        logged.append(row)

        prior = prices_for(history, o["dest"], o["window"])
        label = f"YYZ→{o['dest']} ({dest['city']}) {o['window']} {o['depart']}→{o['return']}"
        detail = f"${best['price_usd']:.0f} · {row['airline']} · via {row['via']}"
        if prior and best["price_usd"] < min(prior):
            new_lows.append(f"- {label}: {detail} (previous low ${min(prior):.0f})")
        if len(prior) < min_n:
            calibrating += 1
            continue
        base = statistics.median(prior)
        drop = 1 - best["price_usd"] / base
        if drop >= threshold:
            deals.append((drop, f"- **{label}**: {detail}, {drop:.0%} below ${base:.0f} median of {len(prior)} obs"))

    # Append after computing baselines so today's prices don't dilute their own comparison.
    new_file = not HISTORY.exists()
    with HISTORY.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerows(logged)

    lines = [f"## {today}"]
    if calibrating:
        lines.append(f"{calibrating}/{len(logged)} route-windows still calibrating (need {min_n} days of history).")
    lines.append(f"### Deals ({threshold:.0%}+ below baseline)")
    lines += [d for _, d in sorted(deals, reverse=True)] or ["- none"]
    lines.append("### New lows")
    lines += new_lows or ["- none"]
    lines.append("### Today's cheapest (no US connections)")
    lines += [f"- {r['dest']} {r['window']}: ${float(r['price_usd']):.0f} · {r['airline']} · via {r['via']}"
              for r in sorted(logged, key=lambda r: float(r["price_usd"]))]
    if no_option:
        lines.append(f"No non-US option in results: {', '.join(no_option)}")
    report = "\n".join(lines)
    with ALERTS.open("a") as f:
        f.write(report + "\n\n")
    print(report)


if __name__ == "__main__":
    main(sys.argv[1])
