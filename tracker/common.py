"""Shared helpers: polite HTTP with caching, dates, paths."""
import hashlib
import json
import os
import time
from datetime import date, datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"          # published output (committed to the repo)
CACHE = ROOT / ".cache"       # raw downloads (not committed)
SEED = ROOT / "seed"          # hand-maintained lists (sponsors, Trump filings)

# SEC asks every automated client to identify itself with a contact address.
USER_AGENT = os.environ.get(
    "TRACKER_USER_AGENT", "trump-congress-tracker (contact: kenzo@kenzostudio.co.uk)"
)

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"})
_last_call: dict[str, float] = {}


def _throttle(host: str, min_gap: float) -> None:
    gap = time.time() - _last_call.get(host, 0)
    if gap < min_gap:
        time.sleep(min_gap - gap)
    _last_call[host] = time.time()


def get(url: str, *, cache: bool = True, max_age_h: float = 24 * 365, min_gap: float = 0.12,
        params: dict | None = None, retries: int = 3, **kw) -> bytes:
    """GET a URL, caching the body on disk. Filings never change once published,
    so most callers keep the default (effectively permanent) cache age."""
    key = hashlib.sha1((url + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()
    path = CACHE / "http" / key[:2] / key
    if cache and path.exists() and (time.time() - path.stat().st_mtime) < max_age_h * 3600:
        return path.read_bytes()
    host = url.split("/")[2]
    for attempt in range(retries):
        _throttle(host, min_gap)
        try:
            r = _session.get(url, params=params, timeout=30, **kw)
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        if cache:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
        return r.content
    raise RuntimeError(f"failed: {url}")


def session() -> requests.Session:
    return _session


def parse_us_date(s: str) -> str | None:
    """'09/03/2026' or '9/3/2026' -> '2026-09-03'."""
    s = (s or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def today() -> str:
    return date.today().isoformat()


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=str))
    tmp.replace(path)


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


# Disclosure value ranges (House and Senate use the same bands).
RANGES = [
    (1, 1_000), (1_001, 15_000), (15_001, 50_000), (50_001, 100_000),
    (100_001, 250_000), (250_001, 500_000), (500_001, 1_000_000),
    (1_000_001, 5_000_000), (5_000_001, 25_000_000), (25_000_001, 50_000_000),
    (50_000_001, None),
]


def parse_amount(text: str) -> tuple[int | None, int | None]:
    """'$1,001 - $15,000' -> (1001, 15000); 'Over $50,000,000' -> (50000001, None)."""
    import re
    nums = [int(n.replace(",", "")) for n in re.findall(r"\$\s*([\d,]+)", text or "")]
    if not nums:
        return None, None
    if "over" in (text or "").lower() and len(nums) == 1:
        return nums[0] + 1, None
    if len(nums) == 1:
        return nums[0], nums[0]
    return nums[0], nums[1]


def midpoint(lo: int | None, hi: int | None) -> float | None:
    """Midpoint of a disclosed range; open-ended top band uses its floor."""
    if lo is None:
        return None
    if hi is None:
        return float(lo)
    return (lo + hi) / 2
