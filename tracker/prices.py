"""Daily adjusted close prices from Yahoo Finance's public chart endpoint."""
import json
from bisect import bisect_right
from datetime import datetime, timezone

from .common import get

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
_mem: dict[str, list[tuple[str, float]] | None] = {}


def _yahoo_symbol(t: str) -> str:
    return t.replace(".", "-")  # BRK.B -> BRK-B


def history(ticker: str) -> list[tuple[str, float]] | None:
    """[(YYYY-MM-DD, adj_close), ...] for roughly the last 4 years."""
    t = ticker.upper()
    if t in _mem:
        return _mem[t]
    try:
        raw = get(URL.format(sym=_yahoo_symbol(t)), params={"range": "5y", "interval": "1d"},
                  max_age_h=6, min_gap=0.4)
        res = json.loads(raw)["chart"]["result"][0]
        ts = res.get("timestamp") or []
        adj = (res.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose") or \
            res["indicators"]["quote"][0]["close"]
        rows = [(datetime.fromtimestamp(s, timezone.utc).date().isoformat(), float(p))
                for s, p in zip(ts, adj) if p is not None]
        _mem[t] = rows or None
    except Exception:
        _mem[t] = None
    return _mem[t]


def price_on(ticker: str, day: str) -> float | None:
    """Close on `day`, or the last trading day before it."""
    h = history(ticker)
    if not h or not day:
        return None
    i = bisect_right([d for d, _ in h], day) - 1
    return h[i][1] if i >= 0 else None


def latest(ticker: str) -> tuple[str, float] | None:
    h = history(ticker)
    return h[-1] if h else None
