"""Senate Periodic Transaction Reports from efdsearch.senate.gov.

The Senate site asks visitors to accept its terms before searching (no
commercial use, no credit rating, no solicitation). This collector only runs
when SENATE_ENABLED=1, which the owner sets after reading and accepting them:
https://efdsearch.senate.gov/search/home/
"""
import re
from datetime import date, timedelta
from html import unescape

from .common import midpoint, parse_amount, parse_us_date, session

BASE = "https://efdsearch.senate.gov"


def _strip(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def _start():
    s = session()
    r = s.get(f"{BASE}/search/home/", timeout=30)
    tok = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', r.text).group(1)
    s.post(f"{BASE}/search/home/", data={"prohibition_agreement": "1", "csrfmiddlewaretoken": tok},
           headers={"Referer": f"{BASE}/search/home/"}, timeout=30)
    return s


def _reports(s, since: date):
    out, start = [], 0
    while True:
        csrf = s.cookies.get("csrftoken")
        r = s.post(f"{BASE}/search/report/data/", data={
            "start": str(start), "length": "100", "report_types": "[11]", "filer_types": "[]",
            "submitted_start_date": since.strftime("%m/%d/%Y 00:00:00"), "submitted_end_date": "",
            "candidate_state": "", "senator_state": "", "office_id": "", "first_name": "", "last_name": "",
            "csrfmiddlewaretoken": csrf}, headers={"Referer": f"{BASE}/search/", "X-CSRFToken": csrf}, timeout=30)
        r.raise_for_status()
        j = r.json()
        for row in j.get("data", []):
            first, last, _office, link_html, filed = row[:5]
            href = re.search(r'href="([^"]+)"', link_html)
            out.append({"first": _strip(first), "last": _strip(last), "filed": parse_us_date(filed),
                        "url": BASE + href.group(1) if href else None, "title": _strip(link_html)})
        start += 100
        if start >= int(j.get("recordsTotal", 0)):
            return out


def _parse(html: str) -> list[dict]:
    body = html.split("<tbody>", 1)[-1].split("</tbody>", 1)[0]
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
        cells = [_strip(td) for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) < 8:
            continue
        _n, tdate, owner, ticker, asset, atype, ttype, amount = cells[:8]
        lo, hi = parse_amount(amount)
        tt = ttype.lower()
        rows.append({
            "owner": {"spouse": "spouse", "joint": "joint", "child": "child"}.get(owner.lower(), "self"),
            "asset": asset, "asset_type": atype,
            "ticker": ticker if ticker and ticker != "--" else None,
            "trade_type": "BUY" if "purchase" in tt else "SELL" if "sale" in tt else "EXCHANGE" if "exchange" in tt else ttype,
            "partial": "partial" in tt,
            "traded_date": parse_us_date(tdate), "notified_date": None,
            "size_lower": lo, "size_upper": hi, "size_mid": midpoint(lo, hi),
        })
    return rows


def collect(days: int = 120, max_reports: int | None = None, log=print):
    s = _start()
    reps = _reports(s, date.today() - timedelta(days=days))
    reps.sort(key=lambda r: r["filed"] or "", reverse=True)
    if max_reports:
        reps = reps[:max_reports]
    trades, unparsed = [], []
    for i, rep in enumerate(reps):
        if not rep["url"] or "/paper/" in rep["url"]:
            unparsed.append({**rep, "reason": "scanned paper filing"})
            continue
        try:
            r = s.get(rep["url"], timeout=30)
            rows = _parse(r.text)
        except Exception as e:
            unparsed.append({**rep, "reason": f"error: {e}"})
            continue
        if not rows:
            unparsed.append({**rep, "reason": "no rows parsed"})
        for row in rows:
            trades.append({"chamber": "Senate", "politician_name": f"{rep['first']} {rep['last']}",
                           "first": rep["first"], "last": rep["last"], "state": None,
                           "reported_date": rep["filed"], "source_url": rep["url"], **row})
        if (i + 1) % 25 == 0:
            log(f"  senate: {i + 1}/{len(reps)} reports")
    return trades, unparsed
