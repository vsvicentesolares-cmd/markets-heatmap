#!/usr/bin/env python3
"""Daily market data for the Daily Markets Brief (written to data/markets.json).

- Indices, FX, commodities: Yahoo Finance daily closes (yfinance). Changes vs the last close
  on or before the same calendar date 1, 6 and 12 months earlier; YTD vs last close of prior year.
- US Treasury par yield curve (home.treasury.gov CSV): latest + 1M/3M/6M/1Y-ago curves.
- US inflation: CPI and core CPI from the BLS public API (NSA for y/y, SA for m/m, as BLS reports them);
  PCE and core PCE from FRED (SA, BEA basis). 13 months of y/y plus latest m/m.
- UK 5Y and 10Y par yields (Bank of England IADB): latest + 1M/3M/6M/1Y ago.
Each block is independent: a failure is recorded in "errors" and the rest still builds.
"""
import datetime as dt
import io
import json
import os
import sys
import time

import pandas as pd
import requests
import yfinance as yf
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = {"User-Agent": "Mozilla/5.0 (compatible; markets-brief/1.0; personal dashboard)"}

INDICES = [("S&P 500", "^GSPC"), ("Nasdaq 100", "^NDX"), ("Dow Jones", "^DJI"), ("IBEX 35", "^IBEX"),
           ("FTSE 100", "^FTSE"), ("S&P/BMV IPC", "^MXX"), ("Euro Stoxx 50", "^STOXX50E"), ("Nikkei 225", "^N225")]
FX = [("USD/MXN", "MXN=X"), ("EUR/USD", "EURUSD=X"), ("GBP/USD", "GBPUSD=X"), ("DXY (context)", "DX-Y.NYB")]
CMD = [("Brent", "BZ=F", "USD/bbl, front-month future"), ("WTI", "CL=F", "USD/bbl, front-month future"),
("Gold", "GC=F", "USD/oz, front-month future"),
       ("Copper", "HG=F", "USD/lb, front-month future")]
UST_COLS = [("3M", "3 Mo"), ("6M", "6 Mo"), ("1Y", "1 Yr"), ("2Y", "2 Yr"), ("5Y", "5 Yr"), ("10Y", "10 Yr"), ("30Y", "30 Yr")]
HORIZONS = [("1M", 1), ("3M", 3), ("6M", 6), ("1Y", 12)]


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def months_back(d, n):
    y, m = d.year, d.month - n
    while m <= 0:
        m += 12
        y -= 1
    day = min(d.day, [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return dt.date(y, m, day)


def on_or_before(series, d):
    """Last value of a date-indexed Series on or before date d -> (date, value) or (None, None)."""
    s = series[series.index <= pd.Timestamp(d)]
    if s.empty:
        return None, None
    return s.index[-1].date(), float(s.iloc[-1])


# Exchange time zone and local close (plus a 20 min buffer) per Yahoo symbol. A bar dated today is only
# kept once that session has closed; bars dated after today's local date (Yahoo labels overnight FX and
# futures sessions with the next date) are always dropped. This keeps intraday snapshots out of "closes".
SESSION = {"^GSPC": ("America/New_York", 16, 20), "^NDX": ("America/New_York", 16, 20),
           "^DJI": ("America/New_York", 16, 20), "^MXX": ("America/Mexico_City", 15, 20),
           "^IBEX": ("Europe/Madrid", 17, 55), "^FTSE": ("Europe/London", 16, 55),
           "^STOXX50E": ("Europe/Berlin", 17, 50), "^N225": ("Asia/Tokyo", 15, 50)}
DEFAULT_SESSION = ("America/New_York", 17, 20)  # FX day and CME/ICE futures settle by ~17:00 New York


def complete_sessions(s, sym, now=None):
    tz, hh, mm = SESSION.get(sym, DEFAULT_SESSION)
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo(tz))
    today = now.date()
    closed = (now.hour, now.minute) >= (hh, mm)
    keep = [(d.date() < today) or (d.date() == today and closed) for d in s.index]
    return s[keep]


def pct(a, b):
    return round((a / b - 1) * 100, 2) if (a is not None and b) else None


def quote_block(rows, dec_map=None):
    syms = [r[1] for r in rows]
    df = yf.download(syms, period="2y", interval="1d", auto_adjust=False, group_by="column",
                     threads=True, progress=False)
    close = df["Close"]
    out = []
    for r in rows:
        name, sym = r[0], r[1]
        rec = {"n": name, "sym": sym}
        if len(r) > 2:
            rec["unit"] = r[2]
        s = close[sym].dropna() if sym in close.columns else pd.Series(dtype=float)
        s.index = pd.to_datetime(s.index).tz_localize(None) if getattr(s.index, "tz", None) else pd.to_datetime(s.index)
        s = s[s.index.dayofweek < 5]  # Yahoo sometimes adds a Sunday bar for FX
        s = complete_sessions(s, sym)
        if len(s) < 2:
            rec["error"] = "no data"
            out.append(rec)
            continue
        last_d, last = s.index[-1].date(), float(s.iloc[-1])
        prev = float(s.iloc[-2])
        rec.update({"d": last_d.isoformat(), "lvl": round(last, (dec_map or {}).get(name, 4 if last < 50 else 2)),
                    "d1": pct(last, prev), "anchors": {}})
        for key, n in (("m1", 1), ("m6", 6), ("y1", 12)):
            ad, av = on_or_before(s, months_back(last_d, n))
            rec[key] = pct(last, av) if ad and (months_back(last_d, n) - ad).days <= 7 else None
            rec["anchors"][key] = ad.isoformat() if ad else None
        ad, av = on_or_before(s, dt.date(last_d.year - 1, 12, 31))
        rec["ytd"] = pct(last, av) if ad and ad.year == last_d.year - 1 else None
        rec["anchors"]["ytd"] = ad.isoformat() if ad else None
        out.append(rec)
    return out


def treasury():
    today = dt.date.today()
    frames = []
    for y in (today.year - 1, today.year):
        url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
               f"{y}/all?type=daily_treasury_yield_curve&field_tdr_date_value={y}&page&_format=csv")
        r = get_retry(url)
        frames.append(pd.read_csv(io.StringIO(r.text)))
    df = pd.concat(frames)
    df["Date"] = pd.to_datetime(df["Date"], format="%m/%d/%Y")
    df = df.set_index("Date").sort_index()

    def curve_at(d):
        sub = df[df.index <= pd.Timestamp(d)]
        if sub.empty:
            return None, None
        row = sub.iloc[-1]
        pts = [{"t": t, "y": (round(float(row[c]), 2) if c in row and pd.notna(row[c]) else None)} for t, c in UST_COLS]
        return sub.index[-1].date(), pts

    last_d, pts = curve_at(df.index[-1])
    hist = []
    for k, n in HORIZONS:
        d, p = curve_at(months_back(last_d, n))
        if d:
            hist.append({"k": k, "d": d.isoformat(), "pts": p})
    return {"asOf": last_d.isoformat(), "curve": pts, "hist": hist,
            "src": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve"}


def get_retry(url, tries=4, timeout=120):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            log("retry", i + 1, url[:80], repr(e)[:120])
            time.sleep(10 * (i + 1))
    raise last


def fred_series(sid):
    r = get_retry(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", tries=2, timeout=45)
    df = pd.read_csv(io.StringIO(r.text))
    dcol = df.columns[0]
    s = pd.to_numeric(df[sid], errors="coerce")
    s.index = pd.to_datetime(df[dcol])
    return s


def bls_series(sid):
    """BLS public API v1 (no key): monthly index levels for the last ~3 years."""
    r = get_retry(f"https://api.bls.gov/publicAPI/v1/timeseries/data/{sid}", tries=3, timeout=60)
    js = r.json()
    if js.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError(f"BLS {sid}: {js.get('status')} {js.get('message')}")
    vals = {}
    for row in js["Results"]["series"][0]["data"]:
        if not row["period"].startswith("M") or row["period"] == "M13":
            continue
        try:
            v = float(row["value"])
        except ValueError:
            continue  # "-" for months BLS did not publish
        vals[pd.Timestamp(int(row["year"]), int(row["period"][1:]), 1)] = v
    s = pd.Series(vals).sort_index()
    full = pd.date_range(s.index.min(), s.index.max(), freq="MS")
    return s.reindex(full)


def infl_from(s, sm, labels):
    valid = s.dropna()
    last = valid.index[-1]
    months = [last - pd.DateOffset(months=i) for i in range(12, -1, -1)]
    pts = []
    for m in months:
        a, b = s.get(m), s.get(m - pd.DateOffset(months=12))
        y = round((a / b - 1) * 100, 2) if (a is not None and b is not None and pd.notna(a) and pd.notna(b)) else None
        pts.append({"m": m.strftime("%Y-%m"), "y": y})
    a, b = sm.get(last), sm.get(last - pd.DateOffset(months=1))
    mom = round((a / b - 1) * 100, 2) if (a is not None and b is not None and pd.notna(a) and pd.notna(b)) else None
    return dict(labels, latest=last.strftime("%Y-%m"), pts=pts, mom=mom)


def cpi_block():
    out = {}
    for key, nsa, sa in (("CPI", "CUUR0000SA0", "CUSR0000SA0"), ("Core CPI", "CUUR0000SA0L1E", "CUSR0000SA0L1E")):
        out[key] = infl_from(bls_series(nsa), bls_series(sa),
                             {"source": "BLS", "yoySeries": nsa, "momSeries": sa})
    return out


def pce_block():
    out = {}
    for key, sid in (("PCE", "PCEPI"), ("Core PCE", "PCEPILFE")):
        s = fred_series(sid)
        out[key] = infl_from(s, s, {"source": "FRED", "yoySeries": sid, "momSeries": sid})
    return out


def fred_block():
    out = {}
    for key, yoy_id, mom_id in (("CPI", "CPIAUCNS", "CPIAUCSL"), ("Core CPI", "CPILFENS", "CPILFESL"),
                                ("PCE", "PCEPI", "PCEPI"), ("Core PCE", "PCEPILFE", "PCEPILFE")):
        s = fred_series(yoy_id)
        valid = s.dropna()
        last = valid.index[-1]
        months = [last - pd.DateOffset(months=i) for i in range(12, -1, -1)]
        pts = []
        for m in months:
            a = s.get(m)
            b = s.get(m - pd.DateOffset(months=12))
            y = round((a / b - 1) * 100, 2) if (a is not None and b is not None and pd.notna(a) and pd.notna(b)) else None
            pts.append({"m": m.strftime("%Y-%m"), "y": y})
        sm = fred_series(mom_id) if mom_id != yoy_id else s
        a, b = sm.get(last), sm.get(last - pd.DateOffset(months=1))
        mom = round((a / b - 1) * 100, 2) if (a is not None and b is not None and pd.notna(a) and pd.notna(b)) else None
        out[key] = {"yoySeries": yoy_id, "momSeries": mom_id, "latest": last.strftime("%Y-%m"), "pts": pts, "mom": mom}
    return out


def boe_block(anchor=None):
    end = dt.date.today()
    start = end - dt.timedelta(days=400)
    f = lambda d: d.strftime("%d/%b/%Y")
    url = ("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes"
           f"&Datefrom={f(start)}&Dateto={f(end)}&SeriesCodes=IUDSNPY,IUDMNPY&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N")
    r = get_retry(url)
    df = pd.read_csv(io.StringIO(r.text))
    df["DATE"] = pd.to_datetime(df["DATE"], format="%d %b %Y")
    df = df.set_index("DATE").sort_index()
    last_d = df.index[-1].date()

    def at(d):
        sub = df[df.index <= pd.Timestamp(d)]
        if sub.empty:
            return None
        row = sub.iloc[-1]
        return {"d": sub.index[-1].date().isoformat(), "5Y": round(float(row["IUDSNPY"]), 2),
                "10Y": round(float(row["IUDMNPY"]), 2)}

    base = anchor or last_d
    return {"latest": at(last_d), "hist": {k: at(months_back(base, n)) for k, n in HORIZONS},
            "src": "Bank of England IADB, IUDSNPY (5Y) and IUDMNPY (10Y) nominal par yields"}


def main():
    os.makedirs(DATA, exist_ok=True)
    out = {"v": 1, "generatedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "errors": [],
           "method": "Yahoo Finance daily closes; 1M/6M/1Y vs last close on or before the same date 1/6/12 months "
                     "earlier (null if no close within 7 days); YTD vs last close of the prior year."}
    blocks = [("indices", lambda: quote_block(INDICES, {n: 2 for n, _ in INDICES})),
              ("fx", lambda: quote_block(FX, {"USD/MXN": 4, "EUR/USD": 5, "GBP/USD": 5, "DXY (context)": 3})),
              ("cmd", lambda: quote_block(CMD, {"Copper": 3})),
              ("ust", treasury),
              ("ukBoE", lambda: boe_block(dt.date.fromisoformat(out["ust"]["asOf"]) if "ust" in out else None)),
              ("usCPI", cpi_block), ("usPCE", pce_block)]
    for key, fn in blocks:
        try:
            out[key] = fn()
            log(key, "ok")
        except Exception as e:
            log(key, "failed:", repr(e))
            out["errors"].append(f"{key}: {repr(e)[:200]}")
    if "usCPI" in out or "usPCE" in out:  # one combined block for the brief
        out["usInflation"] = dict(out.get("usCPI", {}), **out.get("usPCE", {}))
    with open(os.path.join(DATA, "markets.json"), "w") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    if len(out["errors"]) == len(blocks):
        sys.exit(1)


if __name__ == "__main__":
    main()
