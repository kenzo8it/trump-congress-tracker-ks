# Trump & Congress tracker

A free, self-built replacement for InsiderCat-style data. Every day after the US
market closes, GitHub Actions collects public filings, works out estimated
positions and total returns, and commits the results to `data/`. Kenzo's hourly
Claude news task reads these files.

## What it collects

| Data | Source | Notes |
|---|---|---|
| House trades | House Clerk Periodic Transaction Reports (PTRs) | Electronic filings are parsed; scanned paper filings are listed in `data/meta.json` as unparsed |
| Senate trades | Senate eFD (Electronic Financial Disclosure) | Off until `SENATE_ENABLED` is set to `1` (see below) |
| Corporate insider trades | SEC EDGAR Form 4 | Open-market buys and sells only, last 30 days |
| Committee assignments | [unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators) | Used to flag trades in sectors a member's committee oversees |
| Company sector | SEC company records (SIC — Standard Industrial Classification — codes) | |
| Prices | Yahoo Finance daily adjusted close | |
| Federal contracts | USASpending.gov new awards of $25m or more, last 90 days | Defence awards appear with up to a 90-day delay |
| Trump trades | `seed/trump_trades.csv`, from news reports of his OGE (Office of Government Ethics) 278-T filings | Partial; the filings are scanned PDFs |
| Trump sponsors | `seed/sponsors.csv` (White House Ballroom donors, Trump Accounts backers) | Hand-maintained |

## How returns are worked out

Disclosures give value ranges, not exact amounts, so each trade uses the
midpoint of its range. Total return runs from the closing price on the trade
date to the latest close. Positions are rebuilt from the last 400 days of
trades, so holdings bought before that are missing. Every figure is an estimate.

## Output files

- `data/by_ticker/{TICKER}.json` — everything about one stock: Congress trades and estimated holders (with committee overlaps), insider trades, Trump trades, sponsor links, contracts
- `data/by_ticker/index.json` — list of stocks with data
- `data/congress_trades_recent.json` — Congress trades reported in the last 60 days
- `data/congress_ranking.json` — members ranked by estimated total return
- `data/politicians/{BIOGUIDE_ID}.json` — one member's positions and trades
- `data/insider_trades.json`, `data/trump_trades.json`, `data/trump_sponsors.json`, `data/contracts.json`
- `data/meta.json` — run time, counts, coverage notes and filings that couldn't be read

## Running it

- **Automatically:** every day at 22:17 UTC via `.github/workflows/daily.yml`.
- **By hand:** GitHub → Actions → "Daily data build" → Run workflow.
- **Locally:** `pip install -r requirements.txt && python -m tracker.build` (add `--quick` for a small test).

## Turning on Senate trades

The Senate search site asks users to accept its terms first (no commercial use,
no credit rating, no fundraising solicitation). After reading them at
<https://efdsearch.senate.gov/search/home/>, add a repository variable
`SENATE_ENABLED` = `1` under Settings → Secrets and variables → Actions → Variables.

## Updating the hand-kept lists

Edit `seed/trump_trades.csv` when new Trump 278-T filings are reported, and
`seed/sponsors.csv` when new donor lists come out. Each row keeps its source link.

Not financial advice. For personal research only.
