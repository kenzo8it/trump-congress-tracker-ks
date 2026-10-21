"""Daily build: collect everything, compute estimated positions and total
returns, and write JSON files under data/.

    python -m tracker.build            # full run (what GitHub Actions runs)
    python -m tracker.build --quick    # small sample, for testing
"""
import argparse
import csv
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from . import contracts, house, insider, prices, reference, senate
from .common import DATA, SEED, midpoint, read_json, today, write_json


def pct(a, b):
    """Total return from price a to price b, as a percentage."""
    if not a or not b:
        return None
    return round((b / a - 1) * 100, 1)


def avg_price(ticker, start, end):
    h = prices.history(ticker) or []
    vals = [p for d, p in h if start <= d <= end]
    return sum(vals) / len(vals) if vals else None


def days_between(a, b):
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except (TypeError, ValueError):
        return None


def enrich_price(row, ticker_key="ticker", date_key="traded_date"):
    t = row.get(ticker_key)
    last = prices.latest(t) if t else None
    p0 = prices.price_on(t, row.get(date_key)) if t and row.get(date_key) else None
    row["price_at_trade"] = round(p0, 2) if p0 else None
    row["price_now"] = round(last[1], 2) if last else None
    row["price_now_date"] = last[0] if last else None
    row["total_return_pct"] = pct(p0, last[1]) if (p0 and last) else None
    return row


def build_positions(trades):
    """Estimate open positions per politician from their disclosed trades.
    Each trade's size is the midpoint of its disclosed range; shares are
    size / price on the trade date. Sells reduce shares (never below zero)."""
    book = defaultdict(lambda: {"shares": 0.0, "cost": 0.0, "first_buy": None, "last_trade": None})
    for t in sorted(trades, key=lambda r: r.get("traded_date") or ""):
        if not t.get("ticker") or not t.get("bioguide") or not t.get("size_mid") or not t.get("price_at_trade"):
            continue
        b = book[(t["bioguide"], t["ticker"])]
        sh = t["size_mid"] / t["price_at_trade"]
        if t["trade_type"] == "BUY":
            b["shares"] += sh
            b["cost"] += t["size_mid"]
            b["first_buy"] = b["first_buy"] or t["traded_date"]
        elif t["trade_type"] == "SELL" and b["shares"] > 0:
            sold = min(sh, b["shares"])
            b["cost"] -= b["cost"] * (sold / b["shares"])
            b["shares"] -= sold
        b["last_trade"] = t["traded_date"]
    out = defaultdict(list)
    for (bio, tick), b in book.items():
        if b["shares"] <= 0.0001:
            continue
        last = prices.latest(tick)
        if not last:
            continue
        avg = b["cost"] / b["shares"]
        value = b["shares"] * last[1]
        out[bio].append({
            "ticker": tick,
            "est_shares": round(b["shares"], 1),
            "avg_price": round(avg, 2),
            "price_now": round(last[1], 2),
            "est_position_value": round(value),
            "total_return_pct": pct(avg, last[1]),
            "total_return_usd": round(value - b["cost"]),
            "first_buy": b["first_buy"],
            "last_trade": b["last_trade"],
        })
    return out


def read_csv(name):
    try:
        with open(SEED / name) as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def trump_trades():
    rows = []
    for r in read_csv("trump_trades.csv"):
        lo = int(r["size_lower"]) if r["size_lower"] else None
        hi = int(r["size_upper"]) if r["size_upper"] else None
        row = {
            "ticker": r["ticker"], "company": r["company"], "trade_type": r["trade_type"],
            "traded_date": r["traded_date"] or None,
            "period": f"{r['period_start']} to {r['period_end']}" if r["period_start"] else None,
            "size_lower": lo, "size_upper": hi, "size_mid": midpoint(lo, hi),
            "filing": r["filing"], "source_url": r["source"],
        }
        last = prices.latest(r["ticker"])
        if row["traded_date"]:
            p0 = prices.price_on(r["ticker"], row["traded_date"])
            basis = "price on trade date"
        else:  # trades only reported as a quarterly total: use the average price over that period
            p0 = avg_price(r["ticker"], r["period_start"], r["period_end"])
            basis = "average price over the period"
        row["price_at_trade"] = round(p0, 2) if p0 else None
        row["price_basis"] = basis
        row["price_now"] = round(last[1], 2) if last else None
        row["total_return_pct"] = pct(p0, last[1]) if (p0 and last) else None
        rows.append(row)
    return rows


def sponsors():
    rows = []
    for r in read_csv("sponsors.csv"):
        row = {"company": r["company"], "ticker": r["ticker"] or None, "project": r["project"],
               "involvement": r["involvement"], "involvement_date": r["involvement_date"], "source_url": r["source"]}
        if row["ticker"]:
            enrich_price(row, date_key="involvement_date")
            row["return_since_involvement_pct"] = row.pop("total_return_pct")
            row["price_at_involvement"] = row.pop("price_at_trade")
        rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="small sample for testing")
    a = ap.parse_args()
    q = a.quick
    house_days = int(os.environ.get("HOUSE_DAYS", 30 if q else 400))
    insider_days = int(os.environ.get("INSIDER_DAYS", 2 if q else 30))
    log = print

    log("legislators and committees")
    legs = reference.legislators()
    by_bio = {l["bioguide"]: l for l in legs}

    log("House trades")
    h_trades, h_unparsed = house.collect(days=house_days, max_filings=40 if q else None, log=log)
    s_trades, s_unparsed = [], []
    if os.environ.get("SENATE_ENABLED") == "1":
        log("Senate trades")
        s_trades, s_unparsed = senate.collect(days=house_days, max_reports=10 if q else None, log=log)

    log("matching members, sectors, prices")
    trades = []
    sector_cache = {}
    for t in h_trades + s_trades:
        m = (reference.match_house(legs, t["first"], t["last"], t.get("state_district"))
             if t["chamber"] == "House" else reference.match_senate(legs, t["first"], t["last"], t.get("state")))
        t["bioguide"] = m["bioguide"] if m else None
        t["politician_name"] = m["name"] if m else t["politician_name"]
        t["party"] = m["party"] if m else None
        t["state"] = m["state"] if m else (t.get("state_district") or "")[:2] or None
        if t.get("ticker"):
            if t["ticker"] not in sector_cache:
                sector_cache[t["ticker"]] = reference.company_info(t["ticker"])
            info = sector_cache[t["ticker"]]
            t["company_name"] = info.get("name") or t.get("asset")
            t["sector"] = info.get("sector")
            t["committee_overlap"] = sorted({
                c["name"] for c in (m["committees"] if m else [])
                if len(c["code"]) == 4 and info.get("sector") and info["sector"] in c["sectors"]
            })
            enrich_price(t)
        t["days_to_disclose"] = days_between(t.get("traded_date"), t.get("reported_date"))
        t["late_filing"] = (t["days_to_disclose"] or 0) > 45
        for k in ("first", "last"):
            t.pop(k, None)
        trades.append(t)

    positions = build_positions(trades)

    log("insider trades (SEC Form 4)")
    ins = insider.collect(days=insider_days, max_filings=150 if q else None, log=log)
    for r in ins:
        enrich_price(r)

    log("Trump trades and sponsors (seed lists)")
    tt = trump_trades()
    sp = sponsors()

    log("federal contracts (USASpending)")
    try:
        con = contracts.collect(days=90, min_amount=25e6, pages=2 if q else 10, log=log)
    except Exception as e:
        log(f"  contracts failed: {e}")
        con = read_json(DATA / "contracts.json", {}).get("awards", [])

    # ---------- outputs ----------
    stamp = datetime.now(timezone.utc).isoformat(timespec="minutes")
    notes = {
        "returns": "Total returns are estimates: disclosures give value ranges, so each trade uses the midpoint; "
                   "returns run from the closing price on the trade date to the latest close (Yahoo Finance, adjusted).",
        "positions": f"Positions are rebuilt from the last {house_days} days of disclosed trades only, so older holdings are missing.",
        "trump": "Trump trades come from news reports of his OGE 278-T filings (seed/trump_trades.csv), not the full filings, "
                 "which are scanned PDFs with thousands of lines. Treat them as partial.",
        "senate": "Senate trades included." if s_trades else "Senate trades not yet included.",
        "disclosure_lag": "Members of Congress have up to 45 days to disclose a trade.",
    }
    cutoff60 = (date.today() - timedelta(days=60)).isoformat()
    recent = sorted([t for t in trades if (t.get("reported_date") or "") >= cutoff60],
                    key=lambda t: (t.get("reported_date") or "", t.get("traded_date") or ""), reverse=True)

    write_json(DATA / "congress_trades_recent.json", {"updated": stamp, "notes": notes, "trades": recent})
    write_json(DATA / "insider_trades.json", {"updated": stamp, "notes": notes, "trades": ins})
    write_json(DATA / "trump_trades.json", {"updated": stamp, "notes": notes, "trades": tt})
    write_json(DATA / "trump_sponsors.json", {"updated": stamp, "involvements": sp})
    write_json(DATA / "contracts.json", {"updated": stamp, "awards": con})

    # Congress ranking and per-member files
    ranking = []
    for bio, pos in positions.items():
        l = by_bio.get(bio, {})
        cost = sum(p["est_position_value"] - p["total_return_usd"] for p in pos)
        value = sum(p["est_position_value"] for p in pos)
        ranking.append({
            "bioguide": bio, "name": l.get("name"), "party": l.get("party"), "chamber": l.get("chamber"),
            "state": l.get("state"), "positions": len(pos), "est_portfolio_value": round(value),
            "total_return_pct": pct(cost, value) if cost > 0 else None,
            "total_return_usd": round(value - cost),
        })
        write_json(DATA / "politicians" / f"{bio}.json", {
            "updated": stamp, "notes": notes, "member": l,
            "positions": sorted(pos, key=lambda p: -p["est_position_value"]),
            "trades": [t for t in trades if t.get("bioguide") == bio],
        })
    ranking.sort(key=lambda r: -(r["total_return_pct"] or -999))
    write_json(DATA / "congress_ranking.json", {"updated": stamp, "notes": notes, "ranking": ranking})

    # Per-ticker files: everything the hourly news task needs for one stock.
    tickers = defaultdict(lambda: {"congress_trades": [], "congress_holders": [], "insider_trades": [],
                                   "trump_trades": [], "trump_sponsor": [], "contracts": []})
    for t in trades:
        if t.get("ticker"):
            tickers[t["ticker"]]["congress_trades"].append(t)
    for bio, pos in positions.items():
        l = by_bio.get(bio, {})
        for p in pos:
            overlap = sorted({c["name"] for c in l.get("committees", []) if len(c["code"]) == 4
                              and (sector_cache.get(p["ticker"]) or {}).get("sector") in c["sectors"]})
            tickers[p["ticker"]]["congress_holders"].append({
                "name": l.get("name"), "party": l.get("party"), "chamber": l.get("chamber"), "state": l.get("state"),
                "committee_overlap": overlap, **p})
    for r in ins:
        tickers[r["ticker"]]["insider_trades"].append(r)
    for r in tt:
        tickers[r["ticker"]]["trump_trades"].append(r)
    for r in sp:
        if r.get("ticker"):
            tickers[r["ticker"]]["trump_sponsor"].append(r)
    for r in con:
        if r.get("ticker"):
            tickers[r["ticker"]]["contracts"].append(r)

    idx = []
    for tick, d in tickers.items():
        info = sector_cache.get(tick) or reference.company_info(tick)
        last = prices.latest(tick)
        d["congress_trades"].sort(key=lambda r: r.get("traded_date") or "", reverse=True)
        d["insider_trades"].sort(key=lambda r: r.get("traded_date") or "", reverse=True)
        write_json(DATA / "by_ticker" / f"{tick.replace('/', '-')}.json", {
            "updated": stamp, "ticker": tick, "company": info, "price_now": round(last[1], 2) if last else None,
            "price_date": last[0] if last else None, "notes": notes, **d})
        idx.append({"ticker": tick, "company": info.get("name"), "sector": info.get("sector"),
                    **{k: len(v) for k, v in d.items()}})
    write_json(DATA / "by_ticker" / "index.json", {"updated": stamp, "tickers": sorted(idx, key=lambda r: r["ticker"])})

    write_json(DATA / "meta.json", {
        "updated": stamp, "notes": notes,
        "counts": {"congress_trades": len(trades), "recent_congress_trades": len(recent), "members_with_positions": len(positions),
                   "insider_trades": len(ins), "trump_trades": len(tt), "sponsor_involvements": len(sp),
                   "contracts": len(con), "tickers": len(idx)},
        "unparsed_filings": h_unparsed + s_unparsed,
        "files": {
            "per_stock": "data/by_ticker/{TICKER}.json",
            "stock_list": "data/by_ticker/index.json",
            "congress_recent": "data/congress_trades_recent.json",
            "congress_ranking": "data/congress_ranking.json",
            "per_member": "data/politicians/{BIOGUIDE_ID}.json",
            "insider": "data/insider_trades.json",
            "trump": "data/trump_trades.json",
            "sponsors": "data/trump_sponsors.json",
            "contracts": "data/contracts.json",
        },
    })
    log(f"done: {len(trades)} congress trades, {len(positions)} members with positions, "
        f"{len(ins)} insider trades, {len(idx)} tickers, {len(h_unparsed) + len(s_unparsed)} unparsed filings")


if __name__ == "__main__":
    main()
