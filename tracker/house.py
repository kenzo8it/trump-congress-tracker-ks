"""House of Representatives Periodic Transaction Reports (PTRs).

Source: the Clerk's yearly index zip lists every filing; electronic PTRs are
text PDFs we parse. Paper filings are scanned images and are recorded as
'unparsed' so the gap is visible rather than silently dropped.
"""
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta

import pdfplumber

from .common import get, midpoint, parse_amount, parse_us_date

BASE = "https://disclosures-clerk.house.gov/public_disc"

TICKER = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6})\)")


def index(year: int) -> list[dict]:
    raw = get(f"{BASE}/financial-pdfs/{year}FD.zip", max_age_h=6)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        xml = z.read(f"{year}FD.xml")
    out = []
    for m in ET.fromstring(xml.decode("utf-8-sig")):
        if m.findtext("FilingType") != "P":
            continue
        out.append({
            "doc_id": m.findtext("DocID"),
            "first": (m.findtext("First") or "").strip(),
            "last": (m.findtext("Last") or "").strip(),
            "state_district": m.findtext("StateDst"),
            "filed": parse_us_date(m.findtext("FilingDate")),
            "year": year,
        })
    return out


SUBLINE = re.compile(r"^(F\s+S\s*:|S\s+O\s*:|D\s*:|L\s*:|C\s*:|\*)")
DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")


def _clean(t: str) -> str:
    return t.replace("\x00", "").strip()


def _lines(words, tol=3.0):
    rows = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if rows and abs(rows[-1][0] - w["top"]) <= tol:
            rows[-1][1].append(w)
        else:
            rows.append([w["top"], [w]])
    return [sorted(ws, key=lambda w: w["x0"]) for _, ws in rows]


def parse_pdf(pdf_bytes: bytes) -> list[dict] | None:
    """Column-aware parse of an electronic PTR. Returns None for scanned paper
    filings (no text layer)."""
    records, cols = [], None
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if not any((pg.extract_text() or "").strip() for pg in pdf.pages):
            return None
        for pg in pdf.pages:
            words = [dict(w, text=_clean(w["text"])) for w in pg.extract_words()]
            words = [w for w in words if w["text"]]
            body_top = 0.0
            hdr = [w for w in words if w["text"] == "Transaction"]
            if hdr:
                htop = hdr[0]["top"]
                hl = {w["text"]: w for w in words if abs(w["top"] - htop) < 3}
                if "Asset" in hl and "Amount" in hl:
                    dates = sorted([w for w in words if abs(w["top"] - htop) < 3 and w["text"] == "Date"], key=lambda w: w["x0"])
                    cols = {
                        "asset": hl["Asset"]["x0"] - 2,
                        "tx": hl["Transaction"]["x0"] - 2,
                        "date": (dates[0]["x0"] - 2) if dates else hl["Transaction"]["x0"] + 60,
                        "notif": hl.get("Notification", hl["Amount"])["x0"] - 2,
                        "amount": hl["Amount"]["x0"] - 2,
                        "cap": hl.get("Cap.", {"x0": 9999})["x0"] - 2,
                    }
                    body_top = htop + 20
            if not cols:
                continue
            cur, open_asset = None, False
            for line in _lines([w for w in words if w["top"] > body_top]):
                text = " ".join(w["text"] for w in line)
                if text.startswith("* For the complete list") or text.startswith("I CERTIFY"):
                    break
                col = {"owner": [], "asset": [], "tx": [], "date": [], "notif": [], "amount": []}
                for w in line:
                    x = w["x0"]
                    k = ("owner" if x < cols["asset"] else "asset" if x < cols["tx"] else "tx" if x < cols["date"]
                         else "date" if x < cols["notif"] else "notif" if x < cols["amount"] else "amount" if x < cols["cap"] else None)
                    if k:
                        col[k].append(w["text"])
                is_main = col["tx"] and col["tx"][0] in ("P", "S", "E") and col["date"] and DATE.match(col["date"][0])
                if is_main:
                    cur = {k: list(v) for k, v in col.items()}
                    records.append(cur)
                    open_asset = True
                elif cur is not None and SUBLINE.match(" ".join(col["asset"]) or text):
                    open_asset = False
                elif cur is not None and open_asset:
                    for k in ("asset", "tx", "amount"):
                        cur[k] += col[k]
    rows = []
    for r in records:
        asset_raw = " ".join(r["asset"])
        atype = re.findall(r"\[([A-Z]{2})\]", asset_raw)
        atype = atype[-1] if atype else None
        tick = TICKER.findall(asset_raw)
        tx = " ".join(r["tx"])
        lo, hi = parse_amount(" ".join(r["amount"]))
        name = re.sub(r"\s*\[[A-Z]{2}\]", "", TICKER.sub("", asset_raw)).strip(" -")
        rows.append({
            "owner": {"SP": "spouse", "JT": "joint", "DC": "child"}.get(" ".join(r["owner"]).strip(), "self"),
            "asset": name,
            "asset_type": atype,
            "ticker": tick[-1] if tick and atype in ("ST", "OP", "EF", "OT", None) else None,
            "trade_type": {"P": "BUY", "S": "SELL", "E": "EXCHANGE"}.get(tx[:1], tx),
            "partial": "partial" in tx,
            "traded_date": parse_us_date(r["date"][0]),
            "notified_date": parse_us_date(r["notif"][0]) if r["notif"] else None,
            "size_lower": lo,
            "size_upper": hi,
            "size_mid": midpoint(lo, hi),
        })
    return rows


def collect(days: int = 120, max_filings: int | None = None, log=print) -> tuple[list[dict], list[dict]]:
    """All House PTR transactions filed in the last `days` days."""
    since = (date.today() - timedelta(days=days)).isoformat()
    years = sorted({date.today().year, (date.today() - timedelta(days=days)).year})
    filings = [f for y in years for f in index(y) if (f["filed"] or "") >= since]
    filings.sort(key=lambda f: f["filed"], reverse=True)
    if max_filings:
        filings = filings[:max_filings]
    trades, unparsed = [], []
    for i, f in enumerate(filings):
        url = f"{BASE}/ptr-pdfs/{f['year']}/{f['doc_id']}.pdf"
        try:
            rows = parse_pdf(get(url))
        except Exception as e:  # keep going; record the miss
            unparsed.append({**f, "url": url, "reason": f"error: {e}"})
            continue
        if rows is None or not rows:
            unparsed.append({**f, "url": url, "reason": "scanned paper filing" if rows is None else "no rows parsed"})
            continue
        for r in rows:
            trades.append({
                "chamber": "House",
                "politician_name": f"{f['first']} {f['last']}",
                "first": f["first"], "last": f["last"],
                "state_district": f["state_district"],
                "reported_date": f["filed"],
                "source_url": url,
                **r,
            })
        if (i + 1) % 25 == 0:
            log(f"  house: {i + 1}/{len(filings)} filings")
    return trades, unparsed
