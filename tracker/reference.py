"""Reference data: members of Congress, committee assignments, company sectors."""
import json
import re

import yaml

from .common import get

LEG = "https://unitedstates.github.io/congress-legislators/legislators-current.json"
RAW = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# Which industries each committee oversees (by parent committee code).
COMMITTEE_SECTORS = {
    "HSAG": ["Agriculture & Food"], "SSAF": ["Agriculture & Food"],
    "HSAS": ["Defense & Aerospace"], "SSAS": ["Defense & Aerospace"],
    "HSBA": ["Financials", "Real Estate"], "SSBK": ["Financials", "Real Estate"],
    "HSIF": ["Energy & Utilities", "Healthcare", "Technology", "Telecom"],
    "SSCM": ["Technology", "Telecom", "Transport & Autos", "Defense & Aerospace"],
    "HSII": ["Energy & Utilities", "Materials & Mining"], "SSEG": ["Energy & Utilities", "Materials & Mining"],
    "HLIG": ["Defense & Aerospace", "Technology"], "SLIN": ["Defense & Aerospace", "Technology"],
    "HSHM": ["Defense & Aerospace", "Technology"], "SSGA": ["Defense & Aerospace", "Technology"],
    "HSPW": ["Transport & Autos", "Construction & Industrials"],
    "SSEV": ["Energy & Utilities", "Transport & Autos", "Construction & Industrials"],
    "HSSY": ["Technology", "Defense & Aerospace", "Energy & Utilities"],
    "SSHR": ["Healthcare"], "HSVR": ["Healthcare"], "SSVA": ["Healthcare"],
    "HSWM": ["Healthcare", "Financials"], "SSFI": ["Healthcare", "Financials"],
    "HSFA": ["Defense & Aerospace", "Energy & Utilities"], "SSFR": ["Defense & Aerospace", "Energy & Utilities"],
    "HSAP": ["ALL"], "SSAP": ["ALL"],
    "HSSM": ["Retail & Consumer"], "SSSB": ["Retail & Consumer"],
    "HSJU": ["Technology"], "SSJU": ["Technology"],
}


def sector_from_sic(sic: int | None) -> str | None:
    if sic is None:
        return None
    s = int(sic)
    rules = [
        ((3720, 3729), "Defense & Aerospace"), ((3760, 3769), "Defense & Aerospace"),
        ((3480, 3489), "Defense & Aerospace"), ((3795, 3795), "Defense & Aerospace"),
        ((3812, 3812), "Defense & Aerospace"), ((3730, 3732), "Defense & Aerospace"),
        ((6324, 6324), "Healthcare"),
        ((2830, 2836), "Healthcare"), ((3840, 3851), "Healthcare"), ((8000, 8099), "Healthcare"),
        ((5122, 5122), "Healthcare"), ((8731, 8731), "Healthcare"),
        ((1300, 1399), "Energy & Utilities"), ((2900, 2999), "Energy & Utilities"),
        ((4900, 4991), "Energy & Utilities"),
        ((4800, 4899), "Telecom"),
        ((3570, 3579), "Technology"), ((3660, 3679), "Technology"), ((7370, 7379), "Technology"),
        ((3820, 3829), "Technology"),
        ((3710, 3716), "Transport & Autos"), ((4000, 4799), "Transport & Autos"),
        ((100, 999), "Agriculture & Food"), ((2000, 2099), "Agriculture & Food"), ((2870, 2879), "Agriculture & Food"),
        ((1500, 1799), "Construction & Industrials"), ((3500, 3569), "Construction & Industrials"),
        ((3580, 3659), "Construction & Industrials"), ((3680, 3709), "Construction & Industrials"),
        ((1000, 1499), "Materials & Mining"), ((2800, 2869), "Materials & Mining"), ((3300, 3399), "Materials & Mining"),
        ((6500, 6553), "Real Estate"), ((6798, 6798), "Real Estate"),
        ((6000, 6799), "Financials"),
        ((5200, 5999), "Retail & Consumer"), ((2100, 2399), "Retail & Consumer"), ((7000, 7999), "Retail & Consumer"),
    ]
    for (lo, hi), name in rules:
        if lo <= s <= hi:
            return name
    return "Other"


def legislators() -> list[dict]:
    people = json.loads(get(LEG, max_age_h=24))
    committees = yaml.safe_load(get(f"{RAW}/committees-current.yaml", max_age_h=24))
    names = {}
    for c in committees:
        names[c["thomas_id"]] = c["name"]
        for sub in c.get("subcommittees", []) or []:
            names[c["thomas_id"] + sub["thomas_id"]] = f"{c['name']} — {sub['name']}"
    membership = yaml.safe_load(get(f"{RAW}/committee-membership-current.yaml", max_age_h=24))
    by_member: dict[str, list[dict]] = {}
    for code, members in membership.items():
        for m in members or []:
            by_member.setdefault(m["bioguide"], []).append({
                "code": code,
                "name": names.get(code, code),
                "parent": code[:4],
                "title": m.get("title"),
                "sectors": COMMITTEE_SECTORS.get(code[:4], []),
            })
    out = []
    for p in people:
        t = p["terms"][-1]
        out.append({
            "bioguide": p["id"]["bioguide"],
            "name": p["name"].get("official_full") or f"{p['name']['first']} {p['name']['last']}",
            "first": p["name"]["first"], "last": p["name"]["last"],
            "nickname": p["name"].get("nickname"),
            "chamber": "Senate" if t["type"] == "sen" else "House",
            "state": t["state"], "district": t.get("district"),
            "party": t.get("party"),
            "committees": sorted(by_member.get(p["id"]["bioguide"], []), key=lambda c: len(c["code"])),
        })
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def match_house(legs: list[dict], first: str, last: str, state_district: str) -> dict | None:
    st, dist = (state_district or "")[:2], (state_district or "")[2:]
    cands = [l for l in legs if l["chamber"] == "House" and l["state"] == st]
    try:
        d = int(dist)
        exact = [l for l in cands if l["district"] == d]
        if len(exact) == 1:
            return exact[0]
    except ValueError:
        pass
    ln = _norm(last.split(",")[0])
    hits = [l for l in cands if _norm(l["last"]) == ln or ln.endswith(_norm(l["last"]))]
    return hits[0] if len(hits) == 1 else None


def match_senate(legs: list[dict], first: str, last: str, state: str | None = None) -> dict | None:
    ln = _norm(last)
    hits = [l for l in legs if l["chamber"] == "Senate" and (_norm(l["last"]) == ln or ln.startswith(_norm(l["last"])))]
    if state:
        hits = [l for l in hits if l["state"] == state] or hits
    if len(hits) > 1:
        fn = _norm(first)[:3]
        hits = [l for l in hits if _norm(l["first"]).startswith(fn) or _norm(l.get("nickname") or "").startswith(fn)] or hits
    return hits[0] if len(hits) == 1 else None


_companies: dict | None = None


def companies() -> dict[str, dict]:
    """ticker -> {cik, name}"""
    global _companies
    if _companies is None:
        raw = json.loads(get(SEC_TICKERS, max_age_h=24 * 7))
        _companies = {}
        for v in raw.values():
            _companies.setdefault(v["ticker"].upper(), {"cik": int(v["cik_str"]), "name": v["title"]})
    return _companies


def company_info(ticker: str) -> dict:
    """ticker -> {name, cik, sic, industry, sector}; empty dict if unknown."""
    c = companies().get(ticker.upper().replace(".", "-")) or companies().get(ticker.upper())
    if not c:
        return {}
    try:
        sub = json.loads(get(SEC_SUBMISSIONS.format(cik=c["cik"]), max_age_h=24 * 30))
        sic = int(sub["sic"]) if sub.get("sic") else None
        return {"name": c["name"], "cik": c["cik"], "sic": sic, "industry": sub.get("sicDescription"),
                "sector": sector_from_sic(sic)}
    except Exception:
        return {"name": c["name"], "cik": c["cik"]}
