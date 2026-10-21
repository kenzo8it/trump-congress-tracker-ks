"""Corporate insider trades from SEC EDGAR Form 4 filings.

Uses EDGAR's daily form index (every filing published that day), then reads
each Form 4's XML. Only open-market purchases (code P) and sales (code S) are
kept: grants, option exercises and tax withholding are not trading signals.
"""
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta

from .common import get

ARCHIVES = "https://www.sec.gov/Archives"


def _v(node, path):
    el = node.find(path)
    return (el.text or "").strip() if el is not None and el.text else None


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def daily_paths(day: date) -> list[str]:
    q = (day.month - 1) // 3 + 1
    url = f"{ARCHIVES}/edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx"
    try:
        idx = get(url, max_age_h=24 * 365 if day < date.today() - timedelta(days=2) else 3).decode("latin1")
    except Exception:
        return []  # weekends / holidays have no index
    paths = {ln.split()[-1] for ln in idx.splitlines() if re.match(r"^4\s{2,}", ln)}
    return sorted(paths)


def parse_filing(txt: str, url: str, filed: str) -> list[dict]:
    i, j = txt.find("<ownershipDocument>"), txt.find("</ownershipDocument>")
    if i == -1:
        return []
    doc = ET.fromstring(txt[i:j + len("</ownershipDocument>")])
    ticker = (_v(doc, "issuer/issuerTradingSymbol") or "").upper().strip()
    if not ticker or ticker in ("NONE", "N/A"):
        return []
    owners = doc.findall("reportingOwner")
    owner = owners[0] if owners else None
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    titles = []
    if rel is not None:
        if _v(rel, "isDirector") in ("1", "true"):
            titles.append("Director")
        if _v(rel, "isOfficer") in ("1", "true"):
            titles.append(_v(rel, "officerTitle") or "Officer")
        if _v(rel, "isTenPercentOwner") in ("1", "true"):
            titles.append("10% owner")
    out = []
    for t in doc.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _v(t, "transactionCoding/transactionCode")
        if code not in ("P", "S"):
            continue
        shares = _num(_v(t, "transactionAmounts/transactionShares/value"))
        price = _num(_v(t, "transactionAmounts/transactionPricePerShare/value"))
        out.append({
            "ticker": ticker,
            "company_name": _v(doc, "issuer/issuerName"),
            "owner_name": _v(owner, "reportingOwnerId/rptOwnerName") if owner is not None else None,
            "owner_title": ", ".join(titles) or None,
            "trade_type": "BUY" if code == "P" else "SELL",
            "traded_date": _v(t, "transactionDate/value"),
            "reported_date": filed,
            "shares": shares,
            "price": price,
            "value": round(shares * price, 2) if shares and price else None,
            "shares_after": _num(_v(t, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")),
            "planned_10b5_1": _v(doc, "aff10b5One") in ("1", "true"),
            "source_url": url,
        })
    return out


def collect(days: int = 30, max_filings: int | None = None, log=print) -> list[dict]:
    trades, n = [], 0
    for k in range(days):
        day = date.today() - timedelta(days=k)
        paths = daily_paths(day)
        for p in paths:
            if max_filings and n >= max_filings:
                return trades
            n += 1
            url = f"{ARCHIVES}/{p}"
            try:
                txt = get(url).decode("latin1")
                trades += parse_filing(txt, url, day.isoformat())
            except Exception as e:
                log(f"  insider: skip {url}: {e}")
        if paths:
            log(f"  insider: {day} {len(paths)} filings, {len(trades)} trades so far")
    return trades
