"""New federal contract awards from USASpending.gov, matched to listed companies.

Note: USASpending lags. Defence awards appear with a delay of up to 90 days,
so this is a record of awards, not breaking news.
"""
import re
from datetime import date, timedelta

from .common import session
from .reference import companies

API = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
SUFFIX = re.compile(r"\b(the|inc|incorporated|corp|corporation|co|company|llc|l\.l\.c|ltd|limited|plc|holdings?|group|lp|l\.p|n\.?v|s\.?a|ag|se|international|intl|technologies|technology|services|systems|usa|us|of|and|&)\b")


def _key(name: str) -> str:
    n = re.sub(r"[^a-z0-9& ]", " ", (name or "").lower())
    n = SUFFIX.sub(" ", n)
    return " ".join(n.split())


def _index() -> dict[str, str]:
    idx = {}
    for t, c in companies().items():
        k = _key(c["name"])
        if k and len(k) > 3:
            idx.setdefault(k, t)
    return idx


def _aliases() -> dict[str, str]:
    """seed/contractor_aliases.csv: subsidiary or trading name -> parent ticker."""
    import csv
    from .common import SEED
    out = {}
    try:
        with open(SEED / "contractor_aliases.csv") as f:
            for row in csv.DictReader(f):
                out[_key(row["name"])] = row["ticker"].strip().upper()
    except FileNotFoundError:
        pass
    return out


def collect(days: int = 90, min_amount: float = 25e6, pages: int = 10, log=print) -> list[dict]:
    idx = _index()
    aliases = _aliases()
    since = (date.today() - timedelta(days=days)).isoformat()
    out, page = [], 1
    while page <= pages:
        body = {
            "filters": {
                "time_period": [{"start_date": since, "end_date": date.today().isoformat(), "date_type": "new_awards_only"}],
                "award_type_codes": ["A", "B", "C", "D"],
                "award_amounts": [{"lower_bound": min_amount}],
            },
            "fields": ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency", "Awarding Sub Agency",
                       "Start Date", "Description", "generated_internal_id"],
            "sort": "Award Amount", "order": "desc", "limit": 100, "page": page,
        }
        r = session().post(API, json=body, timeout=90)
        r.raise_for_status()
        j = r.json()
        for x in j.get("results", []):
            k = _key(x.get("Recipient Name"))
            private = False
            ticker = aliases.get(k) if k in aliases else idx.get(k)
            if k in aliases and not ticker:
                private = True  # listed in the aliases file with no ticker = known private company
            if not ticker and not private:
                for a, t in aliases.items():  # subsidiary names, e.g. "optum public sector solutions"
                    if k.startswith(a):
                        ticker, private = (t or None), not t
                        break
            if not ticker and not private:  # drop trailing words, but never below two words
                parts = k.split()
                for n in range(len(parts) - 1, 1, -1):
                    ticker = idx.get(" ".join(parts[:n]))
                    if ticker:
                        break
            out.append({
                "award_id": x.get("Award ID"),
                "recipient": x.get("Recipient Name"),
                "ticker": ticker,
                "amount": x.get("Award Amount"),
                "agency": x.get("Awarding Agency"),
                "sub_agency": x.get("Awarding Sub Agency"),
                "start_date": x.get("Start Date"),
                "description": (x.get("Description") or "")[:300],
                "source_url": f"https://www.usaspending.gov/award/{x.get('generated_internal_id')}",
            })
        if not j.get("page_metadata", {}).get("hasNext"):
            break
        page += 1
    log(f"  contracts: {len(out)} awards, {sum(1 for o in out if o['ticker'])} matched to listed companies")
    return out
