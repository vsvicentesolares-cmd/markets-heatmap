#!/usr/bin/env python3
"""Daily index heatmap data for the Daily Markets Brief.

Indices: S&P 500, IBEX 35, FTSE 100, S&P/BMV IPC.
- Constituents and sectors: Wikipedia tables, cached in data/members/<ID>.json
  (the cache is used when a Wikipedia table cannot be parsed).
- Prices: Yahoo Finance daily closes via yfinance.
- Size: market cap = shares outstanding (cached, refreshed weekly) x last close.
Output: data/latest.json and data/<asOf>.json.
"""
import datetime as dt
import json
import os
import re
import sys
import time
from collections import Counter
from io import StringIO

import pandas as pd
import requests
import yfinance as yf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
MEM = os.path.join(DATA, "members")
UA = {"User-Agent": "Mozilla/5.0 (compatible; markets-heatmap/1.0; personal dashboard)"}
SHARES_MAX_AGE_DAYS = 7


def yf_spx(t):
    return t.replace(".", "-")


def yf_ibex(t):
    return t if t.endswith(".MC") else t + ".MC"


def yf_ukx(t):
    if t.endswith(".L"):
        return t
    return t.rstrip(".").replace(".", "-") + ".L"


def yf_mex(t):
    t = re.sub(r"[\s\*]", "", t)
    return t if t.endswith(".MX") else t + ".MX"

# FTSE: ICB sector -> ICB industry (keeps the map readable)
ICB = {
    "Technology": ["Software and Computer Services", "Technology Hardware and Equipment"],
    "Telecommunications": ["Telecommunications Equipment", "Telecommunications Service Providers"],
    "Health Care": ["Health Care Providers", "Medical Equipment and Services",
                    "Pharmaceuticals, Biotechnology and Marijuana Producers", "Pharmaceuticals and Biotechnology"],
    "Financials": ["Banks", "Finance and Credit Services", "Investment Banking and Brokerage Services",
                   "Mortgage Real Estate Investment Trusts", "Closed End Investments",
                   "Open End and Miscellaneous Investment Vehicles", "Life Insurance", "Non-life Insurance"],
    "Real Estate": ["Real Estate Investment and Services", "Real Estate Investment Trusts"],
    "Consumer Discretionary": ["Automobiles and Parts", "Consumer Services", "Household Goods and Home Construction",
                               "Leisure Goods", "Personal Goods", "Media", "Retailers", "Travel and Leisure"],
    "Consumer Staples": ["Beverages", "Food Producers", "Tobacco", "Personal Care, Drug and Grocery Stores"],
    "Industrials": ["Construction and Materials", "Aerospace and Defense", "Electronic and Electrical Equipment",
                    "General Industrials", "Industrial Engineering", "Industrial Support Services",
                    "Industrial Transportation"],
    "Basic Materials": ["Industrial Materials", "Industrial Metals and Mining", "Precious Metals and Mining", "Chemicals"],
    "Energy": ["Non-Renewable Energy", "Renewable Energy", "Oil, Gas and Coal", "Alternative Energy"],
    "Utilities": ["Electricity", "Gas, Water and Multi-utilities", "Waste and Disposal Services"],
}
ICB_MAP = {sec.lower(): ind for ind, secs in ICB.items() for sec in secs}

# IBEX: Wikipedia's own labels -> broad groups
IBEX_GROUPS = {
    "Financials": ["financial services", "insurance", "banking", "banks"],
    "Utilities and energy": ["energy", "solar energy", "oil and gas", "electricity", "renewable energy", "utilities"],
    "Industrials and infrastructure": ["construction", "infrastructure", "aviation", "logistics", "manufacturing",
                                       "engineering", "defence", "defense"],
    "Materials": ["steel", "materials", "chemicals"],
    "Consumer": ["textile", "clothes and cosmetics", "tourism", "food", "retail", "hotels", "leisure"],
    "Telecom and tech": ["telecommunications", "information technology", "technology", "media"],
    "Health care": ["pharmaceuticals", "health care", "healthcare"],
    "Real estate": ["real estate"],
}
IBEX_MAP = {lab: g for g, labs in IBEX_GROUPS.items() for lab in labs}


def smap_ukx(s):
    return ICB_MAP.get(s.lower(), s or "Other")


def smap_ibex(s):
    return IBEX_MAP.get(s.lower(), s or "Other")


# S&P/BMV IPC: no free, current, machine-readable list. Composition after the 21 Sep 2026
# rebalance (Alpek in, Televisa out; Volaris in, Cuervo out in Mar 2026). Update after each
# March/September review. "y" lists Yahoo symbols to try in order. Sectors: GICS-style.
MEX_STATIC = [
    ("AC", "Arca Continental", "Consumer Staples", ["AC.MX"]),
    ("ALPEK A", "Alpek", "Materials", ["ALPEKA.MX"]),
    ("ALSEA", "Alsea", "Consumer Discretionary", ["ALSEA.MX"]),
    ("AMX B", "America Movil", "Communication Services", ["AMXB.MX"]),
    ("ASUR B", "Grupo Aeroportuario del Sureste", "Industrials", ["ASURB.MX"]),
    ("BBAJIO O", "Banco del Bajio", "Financials", ["BBAJIOO.MX"]),
    ("BIMBO A", "Grupo Bimbo", "Consumer Staples", ["BIMBOA.MX"]),
    ("BOLSA A", "Bolsa Mexicana de Valores", "Financials", ["BOLSAA.MX"]),
    ("CEMEX CPO", "Cemex", "Materials", ["CEMEXCPO.MX"]),
    ("CHDRAUI B", "Grupo Comercial Chedraui", "Consumer Staples", ["CHDRAUIB.MX"]),
    ("FEMSA UBD", "FEMSA", "Consumer Staples", ["FEMSAUBD.MX"]),
    ("GAP B", "Grupo Aeroportuario del Pacifico", "Industrials", ["GAPB.MX"]),
    ("GCARSO A1", "Grupo Carso", "Industrials", ["GCARSOA1.MX"]),
    ("GCC", "GCC", "Materials", ["GCC.MX"]),
    ("GENTERA", "Gentera", "Financials", ["GENTERA.MX"]),
    ("GFINBUR O", "Grupo Financiero Inbursa", "Financials", ["GFINBURO.MX"]),
    ("GFNORTE O", "Grupo Financiero Banorte", "Financials", ["GFNORTEO.MX"]),
    ("GMEXICO B", "Grupo Mexico", "Materials", ["GMEXICOB.MX"]),
    ("GRUMA B", "Gruma", "Consumer Staples", ["GRUMAB.MX"]),
    ("KIMBER A", "Kimberly-Clark de Mexico", "Consumer Staples", ["KIMBERA.MX"]),
    ("KOF UBL", "Coca-Cola FEMSA", "Consumer Staples", ["KOFUBL.MX"]),
    ("LAB B", "Genomma Lab", "Health Care", ["LABB.MX"]),
    ("LACOMER UBC", "La Comer", "Consumer Staples", ["LACOMERUBC.MX"]),
    ("LIVEPOL C-1", "El Puerto de Liverpool", "Consumer Discretionary", ["LIVEPOLC-1.MX"]),
    ("MEGA CPO", "Megacable", "Communication Services", ["MEGACPO.MX"]),
    ("OMA B", "Grupo Aeroportuario Centro Norte", "Industrials", ["OMAB.MX"]),
    ("ORBIA", "Orbia", "Materials", ["ORBIA.MX"]),
    ("PE&OLES", "Penoles", "Materials", ["PE&OLES.MX"]),
    ("PINFRA", "Promotora y Operadora de Infraestructura", "Industrials", ["PINFRA.MX"]),
    ("Q", "Qualitas", "Financials", ["Q.MX"]),
    ("R A", "Regional", "Financials", ["RA.MX"]),
    ("SIGMAF", "Sigma Foods", "Consumer Staples", ["SIGMAF.MX", "SIGMAFA.MX", "ALFAA.MX"]),
    ("VESTA", "Corporacion Inmobiliaria Vesta", "Real Estate", ["VESTA.MX"]),
    ("VOLAR A", "Volaris", "Industrials", ["VOLARA.MX"]),
    ("WALMEX", "Walmart de Mexico", "Consumer Staples", ["WALMEX.MX"]),
]


INDICES = [
    dict(id="SPX", name="S&P 500", url="https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
         tick=["symbol", "ticker"], ncol=["security", "company"], scol=["gics sector", "sector"],
         lo=490, hi=510, conv=yf_spx),
    dict(id="IBEX", name="IBEX 35", url="https://en.wikipedia.org/wiki/IBEX_35",
         tick=["ticker", "symbol"], ncol=["company", "name"], scol=["sector", "industry"],
         lo=33, hi=37, conv=yf_ibex, smap=smap_ibex),
    dict(id="UKX", name="FTSE 100", url="https://en.wikipedia.org/wiki/FTSE_100_Index",
         tick=["ticker", "epic", "symbol"], ncol=["company", "name"],
         scol=["ftse industry classification benchmark sector", "icb sector", "sector", "industry"],
         lo=98, hi=102, conv=yf_ukx, smap=smap_ukx),
    dict(id="MEXBOL", name="S&P/BMV IPC", static=MEX_STATIC, conv=yf_mex),
]


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def clean(x):
    s = re.sub(r"\[.*?\]", "", str(x)).strip()
    return "" if s.lower() in ("nan", "none") else s


def flat_cols(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [" ".join(clean(c) for c in col if clean(c)) for col in df.columns]
    df.columns = [clean(c) for c in df.columns]
    return df


def pick(cols, wanted):
    low = {c.lower(): c for c in cols}
    for w in wanted:
        for lc, c in low.items():
            if lc == w:
                return c
    for w in wanted:
        for lc, c in low.items():
            if w in lc:
                return c
    return None


def members_from_wikipedia(ix):
    html = requests.get(ix["url"], headers=UA, timeout=30).text
    for df in pd.read_html(StringIO(html)):
        df = flat_cols(df)
        tc = pick(df.columns, ix["tick"])
        if not tc or not (ix["lo"] <= len(df) <= ix["hi"]):
            continue
        nc = pick(df.columns, ix["ncol"])
        sc = pick(df.columns, ix["scol"])
        out = []
        for _, r in df.iterrows():
            t = clean(r[tc])
            if ":" in t:
                t = t.split(":")[-1].strip()
            if not t:
                continue
            out.append({"t": t, "y": ix["conv"](t), "n": clean(r[nc]) if nc else t,
                        "s": clean(r[sc]) if sc else ""})
        if ix["lo"] <= len(out) <= ix["hi"]:
            return out
    raise ValueError("no constituents table matched")


def load_cache(ix):
    p = os.path.join(MEM, ix["id"] + ".json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return []


def save_cache(ix, members):
    os.makedirs(MEM, exist_ok=True)
    with open(os.path.join(MEM, ix["id"] + ".json"), "w") as f:
        json.dump(members, f, ensure_ascii=False, indent=0)


def get_members(ix):
    cache = {m["t"]: m for m in load_cache(ix)}
    if ix.get("static"):
        out = []
        for t, n, sec, alts in ix["static"]:
            m = {"t": t, "n": n, "s": sec, "alts": alts, "y": cache.get(t, {}).get("y") or alts[0]}
            for k in ("sh", "shAt"):
                if k in cache.get(t, {}):
                    m[k] = cache[t][k]
            out.append(m)
        return out, "static list (Sep 2026 review)"
    try:
        fresh = members_from_wikipedia(ix)
        src = "wikipedia"
    except Exception as e:  # keep yesterday's list
        log(ix["id"], "wikipedia failed:", e)
        if not cache:
            return [], "none"
        return list(cache.values()), "cache"
    for m in fresh:  # carry cached shares and sectors forward
        old = cache.get(m["t"], {})
        for k in ("sh", "shAt", "ys"):
            if k in old:
                m[k] = old[k]
    return fresh, src


def refresh_shares(members, today):
    for m in members:
        at = m.get("shAt")
        fresh = at and (today - dt.date.fromisoformat(at)).days < SHARES_MAX_AGE_DAYS
        if fresh and m.get("sh"):
            continue
        sh = None
        try:
            tk = yf.Ticker(m["y"])
            try:
                sh = tk.fast_info.get("shares")
            except Exception:
                sh = None
            if not sh:
                sh = (tk.info or {}).get("sharesOutstanding")
            if not m.get("s") and not m.get("ys"):
                try:
                    m["ys"] = (tk.info or {}).get("sector") or ""
                except Exception:
                    pass
        except Exception as e:
            log("shares", m["y"], e)
        if sh:
            m["sh"] = float(sh)
            m["shAt"] = today.isoformat()
        time.sleep(0.15)


def closes_for(symbols):
    df = yf.download(symbols, period="1mo", interval="1d", auto_adjust=False,
                     group_by="column", threads=True, progress=False)
    if df is None or df.empty:
        return pd.DataFrame()
    c = df["Close"] if "Close" in df.columns.get_level_values(0) else df
    if isinstance(c, pd.Series):
        c = c.to_frame(symbols[0])
    return c


def compute(ix, members, closes):
    rows, last_dates = [], []
    for m in members:
        s = closes[m["y"]].dropna() if m["y"] in closes.columns else pd.Series(dtype=float)
        if len(s) == 0:
            rows.append((m, None, None, None))
            continue
        last_d = s.index[-1].date().isoformat()
        last_dates.append(last_d)
        prev = float(s.iloc[-2]) if len(s) > 1 else None
        rows.append((m, last_d, float(s.iloc[-1]), prev))
    if not last_dates:
        return None
    as_of = Counter(last_dates).most_common(1)[0][0]
    items, caps = [], []
    for m, last_d, close, prev in rows:
        cap = m["sh"] * close if (m.get("sh") and close) else None
        d1 = None
        flag = ""
        if last_d is None:
            flag = "no data"
        elif last_d != as_of:
            flag = "last close " + last_d
        elif prev:
            d1 = round((close / prev - 1) * 100, 2)
        sec = m.get("s") or m.get("ys") or "Other"
        if ix.get("smap"):
            sec = ix["smap"](sec)
        items.append({"t": m["t"], "n": m.get("n") or m["t"], "s": sec,
                      "cap": cap, "d1": d1, "c": round(close, 2) if close else None, "f": flag})
        if cap:
            caps.append(cap)
    tot = sum(caps)
    sectors = sorted({i["s"] for i in items})
    out_items, wsum, wd = [], 0.0, 0.0
    for i in items:
        w = round(i["cap"] / tot * 100, 4) if (i["cap"] and tot) else None
        if w is not None and i["d1"] is not None:
            wsum += w
            wd += w * i["d1"]
        rec = {"t": i["t"], "n": i["n"], "s": sectors.index(i["s"]), "w": w, "d1": i["d1"], "c": i["c"]}
        if i["f"]:
            rec["f"] = i["f"]
        out_items.append(rec)
    out_items.sort(key=lambda r: -(r["w"] or 0))
    return {
        "id": ix["id"], "name": ix["name"], "asOf": as_of, "n": len(out_items),
        "priced": sum(1 for r in out_items if r["d1"] is not None),
        "sized": sum(1 for r in out_items if r["w"] is not None),
        "wd1": round(wd / wsum, 2) if wsum else None,
        "sectors": sectors, "items": out_items,
    }


def main():
    today = dt.datetime.now(dt.timezone.utc).date()
    os.makedirs(DATA, exist_ok=True)
    result = {"v": 1, "generatedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
              "source": "Prices: Yahoo Finance (yfinance). Constituents and sectors: Wikipedia. "
                        "Size: shares outstanding x last close (approximates index weight).",
              "indices": [], "errors": []}
    for ix in INDICES:
        try:
            members, msrc = get_members(ix)
            if not members:
                result["errors"].append(ix["id"] + ": no constituents")
                continue
            syms = sorted({a for m in members for a in (m.get("alts") or [m["y"]])})
            closes = closes_for(syms)
            for m in members:
                for a in (m.get("alts") or [m["y"]]):
                    if a in closes.columns and closes[a].dropna().size:
                        if a != m["y"]:
                            m.pop("sh", None)
                            m.pop("shAt", None)
                        m["y"] = a
                        break
            refresh_shares(members, today)
            block = compute(ix, members, closes)
            if not block:
                result["errors"].append(ix["id"] + ": no prices")
                continue
            block["members"] = msrc
            result["indices"].append(block)
            save_cache(ix, members)
            log(ix["id"], "ok", block["asOf"], block["priced"], "/", block["n"], "wd1", block["wd1"])
        except Exception as e:
            log(ix["id"], "failed:", repr(e))
            result["errors"].append(ix["id"] + ": " + repr(e)[:200])
    if not result["indices"]:
        log("nothing built")
        sys.exit(1)
    as_of = max(b["asOf"] for b in result["indices"])
    result["asOf"] = as_of
    for name in ("latest.json", as_of + ".json"):
        with open(os.path.join(DATA, name), "w") as f:
            json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    log("wrote", as_of)


if __name__ == "__main__":
    main()
