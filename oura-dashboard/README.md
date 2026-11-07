# Oura data, locally

Pull your Oura ring data into a SQLite file on your own machine and view it in a self-contained HTML dashboard. Python standard library only, no third-party requests at view time (Chart.js is inlined).

Try it with synthetic data first: `python3 oura.py demo` → `~/.oura-dashboard/demo_dashboard.html`.

## Read this first: what this can and can't do

The ring has no internet connection. Data path:

```
ring --(Bluetooth)--> Oura app --(upload)--> Oura cloud --(API)--> this tool
```

The Oura API only returns what the **app** has already uploaded. If you stop syncing the app, the API stops getting new days, and this dashboard shows a "data is N days old" banner. Using the API also means logging into your Oura account and accepting Oura's API terms.

So there are three realistic modes:

| Mode | New data daily? | Needs Oura app / account? | Effort |
|---|---|---|---|
| **A. API sync** (`auth` + `daily`) | Yes | App must keep syncing in the background; account + API terms | Low |
| **B. Export & leave** (`import`) | No, one-time snapshot | Once, to request the export | Low |
| **C. Local Bluetooth** (e.g. the community [open_oura](https://github.com/Th0rgal/open_oura) project) | Yes | Once, to extract the ring's auth key; then no cloud | High, unofficial, can break with firmware updates |

This tool handles A and B now. Mode C can feed the same SQLite `records` table later.

## Setup (mode A: API)

1. Create an API application at <https://cloud.ouraring.com/oauth/applications>.
   Set the redirect URI to `http://localhost:8765/callback`. Note the client ID and secret.
2. Run the one-time login:
   ```bash
   python3 oura.py auth
   ```
   Tokens are stored in `~/.oura-dashboard/tokens.json` with mode 600. Oura refresh tokens are single-use. The tool saves the new one every time it refreshes, so don't copy the token file between machines and run both.
3. Pull your full history once, then build the dashboard:
   ```bash
   python3 oura.py sync --days 3650
   python3 oura.py dashboard     # -> ~/.oura-dashboard/dashboard.html
   ```
4. Run it daily (cron, 9:15am):
   ```cron
   15 9 * * * cd /path/to/oura-dashboard && /usr/bin/python3 oura.py daily >> ~/.oura-dashboard/cron.log 2>&1
   ```

Still have a legacy personal access token? `export OURA_PAT=...` skips step 1–2. New ones can no longer be created.
If the token endpoint changes, override it with `OURA_TOKEN_URL`.

## Setup (mode B: export)

Request your data export from your Oura account (membership/account settings, "Export data"), unzip it, then:

```bash
python3 oura.py import ~/Downloads/oura-export/
python3 oura.py dashboard
```

The importer accepts API-style JSON (`{"data": [...]}`), JSON keyed by collection name, and CSV files whose names contain the collection (`daily_sleep`, `readiness`, `activity`, ...). Oura's export format has changed over time. If a file is skipped, check the printed message and rename it, or open an issue with the column headers (not the data).

## Commands

| Command | What it does |
|---|---|
| `auth` | OAuth2 login via local browser redirect |
| `sync [--days N] [--heartrate]` | Fetch the last N days (default 14) of every daily collection; `--heartrate` adds 5-min HR samples |
| `import PATH` | Load an export file or folder |
| `dashboard [--out FILE]` | Write the HTML dashboard |
| `daily` | `sync` + `dashboard` |
| `demo` | Build a dashboard from synthetic data in a separate `demo.db` |

## Data

Everything is stored in `~/.oura-dashboard/oura.db` (override with `OURA_HOME`). One table, raw JSON kept intact, so nothing is lost if you want other metrics later:

```sql
SELECT day, json_extract(data, '$.average_hrv') AS hrv
FROM records WHERE collection = 'sleep' ORDER BY day DESC LIMIT 14;
```

Don't commit `~/.oura-dashboard` or run this in CI. It's health data plus OAuth tokens.
