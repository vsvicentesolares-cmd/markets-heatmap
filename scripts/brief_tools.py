#!/usr/bin/env python3
"""Helper for the Daily Markets Brief scheduled run (keeps the run from rewriting the whole edition).

  prep : python3 brief_tools.py prep --prev PREV.json --date YYYY-MM-DD --out DIR
                                     [--markets markets.json] [--heat latest.json]
         Copies yesterday's edition into DIR/base.json for the new date, applies markets.json
         (index/FX/commodity tables, US Treasury curve and look-backs, US inflation chart),
         drops past calendar events, builds DIR/heat.json, and prints a compact summary.
  merge: python3 brief_tools.py merge --base DIR/base.json --patch PATCH.json --index META.json --out DIR
         Applies the run's patch (new text and any macro changes), runs every quality check and
         writes DIR/edition.json, DIR/index.json (meta/index) and DIR/curves.json. Exit 1 on errors.

Past editions are never modified: the output is always a new document for --date.
"""
import argparse
import copy
import datetime as dt
import json
import re
import sys

NAMES = {"SPX": "S&P 500", "IBEX": "IBEX 35", "UKX": "FTSE 100", "MEXBOL": "S&P/BMV IPC"}
KEYS = ["lvl", "d1", "m1", "m6", "y1", "ytd"]
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
REQUIRED_PATCH = ["tape", "tapeShort", "top", "talk", "learn", "quiz", "factoid", "factoidShort", "notes"]
TEMPLATE_KEYS = ["v", "date", "generatedAt", "dataAsOf", "tape", "top", "calendar", "macro", "indices", "marketsNote",
                 "cmd", "fx", "fxNote", "officials", "spain", "mexico", "deals", "ipos", "talk", "learn", "quiz",
                 "factoid", "notes", "sources"]


def load(p):
    with open(p) as f:
        return json.load(f)


def dump(obj, p, pretty=False):
    with open(p, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1 if pretty else None,
                  separators=None if pretty else (",", ":"))


def short(d):
    y, m, dd = d.split("-")
    return f"{int(dd)} {MON[int(m) - 1]}"


def mlabel(m):
    y, mm = m.split("-")
    return f"{MON[int(mm) - 1]} {y}"


# ---------------------------------------------------------------- prep
def apply_tables(ed, mk, log):
    us = next((r for r in mk.get("indices", []) if r.get("n") == "S&P 500" and r.get("d")), None)
    D = us["d"] if us else None
    for tbl in ("indices", "fx", "cmd"):
        feed = {r["n"]: r for r in mk.get(tbl, []) if r.get("n")}
        for row in ed.get(tbl, []):
            f = feed.get(row["n"])
            if not f or f.get("error") or not f.get("d"):
                log.append(f"MARKET NOT IN FEED (fetch it, send in patch.markets): {row['n']}")
                continue
            for k in KEYS:
                row[k] = f.get(k)
            if row.get("note") and re.search(r"\d{1,2} [A-Z][a-z]{2}", row["note"]):
                row["note"] = re.sub(r"\d{1,2} [A-Z][a-z]{2}", short(f["d"]), row["note"])
            if D and tbl == "indices" and f["d"] != D:
                log.append(f"CHECK DATE: {row['n']} last close {f['d']} vs US {D} (holiday or stale)")
    return D


def apply_us_curve(ed, mk, log):
    ust = mk.get("ust")
    us = next((c for c in ed.get("macro", []) if c.get("c") == "US"), None)
    if not ust or not us:
        log.append("US CURVE NOT IN FEED: fetch the Treasury TextView and send macro.US curve/curveHist in patch")
        return None
    D = ust["asOf"]
    tn = [p["t"] for p in us.get("curve", [])] or [p["t"] for p in ust["curve"]]
    us["curve"] = [p for p in ust["curve"] if p["t"] in tn]
    us["curveAsOf"] = f"{short(D)} · US Treasury par curve"
    us["curveHist"] = [{"k": h["k"], "d": h["d"], "pts": [p for p in h["pts"] if p["t"] in tn]} for h in ust.get("hist", [])]
    y = {p["t"]: p["y"] for p in us["curve"]}
    for r in us.get("rows", []):
        k = r.get("k", "")
        if k.startswith("1Y Treasury") and y.get("1Y") is not None:
            r["v"], r["p"] = f"{y['1Y']:.2f}%", f"{short(D)}, par curve"
        elif k.startswith("2Y / 10Y") and y.get("2Y") is not None and y.get("10Y") is not None:
            r["v"], r["p"] = f"{y['2Y']:.2f}% / {y['10Y']:.2f}%", f"{short(D)}, par curve"
        elif k.startswith("10Y-2Y") and y.get("2Y") is not None and y.get("10Y") is not None:
            bp = round((y["10Y"] - y["2Y"]) * 100)
            r["v"] = f"{bp:+d}bp"
            r["p"] = "positive slope" if bp > 0 else ("inverted" if bp < 0 else "flat")
    return D


def apply_ez(ed, mk, log):
    """Euro area box from the ECB feed: inflation chart (HICP, core) and ECB rate check. Rows stay manual."""
    f = mk.get("ezECB")
    ez = next((c for c in ed.get("macro", []) if c.get("c") == "EZ"), None)
    if not ez:
        return
    if not f or not f.get("HICP"):
        log.append("EZ ECB FEED MISSING (check the GitHub run): keep EZ inflation chart, update rows by hand on release days")
        return
    months = [p["m"] for p in f["HICP"]["pts"]][-13:]
    chart_last = ez["infl"]["series"][0]["pts"][-1]["m"]
    if len(months) < 13 or months[-1] < chart_last:
        log.append(f"EZ ECB FEED STALE (latest {months[-1]} vs chart {chart_last}): chart left as is; check the GitHub run")
        return
    old = {s["n"]: {p["m"]: p["y"] for p in s["pts"]} for s in ez["infl"]["series"]}
    changed = []
    for s in ez["infl"]["series"]:
        fv = {p["m"]: p["y"] for p in (f.get(s["n"]) or {}).get("pts", [])}
        new = [{"m": m, "y": fv.get(m, old[s["n"]].get(m))} for m in months]
        changed += [f"{s['n']} {p['m']}: {old[s['n']].get(p['m'])} -> {p['y']}" for p in new if old[s["n"]].get(p["m"]) != p["y"]]
        s["pts"] = new
    ez["infl"]["asOf"] = f"{mlabel(months[0])} to {mlabel(months[-1])}"
    if changed:
        log.append("EZ INFLATION CHART UPDATED: " + "; ".join(changed[:8]))
    rows = {r["k"]: r for r in ez.get("rows", [])}
    log.append("EZ feed (latest month, y/y): " + json.dumps({k: (f[k]["latest"], f[k]["pts"][-1]["y"]) for k in ("HICP", "Core HICP") if f.get(k)}) +
               " | rows p: " + json.dumps({k: rows[k].get("p") for k in ("HICP", "Core HICP") if k in rows}) +
               " | ECB rates: " + json.dumps(f.get("rates")) + " | box: " + str((ez.get("cb") or {}).get("rate")))


def apply_us_infl(ed, mk, log):
    inf = mk.get("usInflation") or {}
    us = next((c for c in ed.get("macro", []) if c.get("c") == "US"), None)
    if not us or not us.get("infl") or not inf.get("CPI"):
        log.append("US INFLATION NOT IN FEED: on a CPI/PCE release day use the FRED text pages")
        return
    series = us["infl"]["series"]
    old_months = [p["m"] for p in series[0]["pts"]]
    cpi_months = [p["m"] for p in inf["CPI"]["pts"]]
    months = cpi_months if cpi_months[-1] > old_months[-1] else old_months
    changed = []
    for s in series:
        fs = inf.get(s["n"])
        old = {p["m"]: p["y"] for p in s["pts"]}
        fv = {p["m"]: p["y"] for p in fs["pts"]} if fs else {}
        new = []
        for m in months:
            v = fv[m] if (m in fv and fv[m] is not None) else old.get(m)
            if m in fv and fv[m] is None and fs and m <= fs["latest"]:
                v = None  # official gap (e.g. Oct 2025 CPI not collected)
            if old.get(m) != v:
                changed.append(f"{s['n']} {m}: {old.get(m)} -> {v}")
            new.append({"m": m, "y": v})
        s["pts"] = new
    us["infl"]["asOf"] = f"{mlabel(months[0])} to {mlabel(months[-1])}"
    if changed:
        log.append("US INFLATION CHART UPDATED: " + "; ".join(changed[:8]))
    feed_latest = {k: (v["latest"], v["pts"][-1]["y"], v.get("mom")) for k, v in inf.items()}
    rows = {r["k"]: r for r in us.get("rows", [])}
    log.append("US inflation feed (latest month, y/y, m/m): " + json.dumps(feed_latest) +
               " | rows p: " + json.dumps({k: rows[k].get("p") for k in ("CPI", "Core CPI", "PCE", "Core PCE") if k in rows}))


def build_heat(ed, heat, date, mk=None):
    if not heat:
        return None, "heat file not provided"
    try:
        gen = dt.datetime.strptime(heat["generatedAt"], "%Y-%m-%dT%H:%MZ")
    except Exception:
        return None, "heat file has no valid generatedAt"
    if gen.date() != dt.datetime.now(dt.timezone.utc).date():
        return None, f"heat file is stale (built {heat['generatedAt']}, not today UTC)"
    idx = {r["n"]: r.get("d1") for r in ed.get("indices", [])}
    check = [{"id": b["id"], "wd1": b.get("wd1"), "idx": idx.get(NAMES.get(b["id"], ""))} for b in heat.get("indices", [])]
    doc = {"date": date, "asOf": heat.get("asOf"), "generatedAt": heat["generatedAt"],
           "source": "Yahoo Finance closes via github.com/vsvicentesolares-cmd/markets-heatmap; constituents and "
                     "sectors from Wikipedia (IPC: static list from the latest S&P/BMV review); size = shares x close",
           "indices": heat.get("indices", []), "check": check, "errors": heat.get("errors", [])}
    gaps = [f"{c['id']} {c['wd1']} vs {c['idx']}" for c in check
            if c["wd1"] is not None and c["idx"] is not None and abs(c["wd1"] - c["idx"]) > 0.6]
    # A map dated before its index's last close, or with a big unpriced share, is stale even when the weighted
    # move happens to sit near the index move (19 Nov 2026: IBEX and FTSE maps showed 17 Nov, gaps under 0.6pp).
    mkd = {r["n"]: r.get("d") for r in (mk or {}).get("indices", [])}
    stale = []
    for b in heat.get("indices", []):
        want = mkd.get(NAMES.get(b["id"], "")) or b.get("idxAsOf")
        if want and b.get("asOf", "") < want:
            stale.append(f"{b['id']} map dated {b.get('asOf')} but index closed {want}")
        unp = b.get("unpricedW")
        if unp is None:
            unp = sum((i.get("w") or 0) for i in b.get("items", []) if i.get("d1") is None)
        if unp > 10:
            stale.append(f"{b['id']} {round(unp, 1)}% of weight unpriced")
    msgs = []
    if stale:
        msgs.append("HEAT STALE (tell Vicente; re-run the heatmap-data workflow): " + "; ".join(stale))
    if gaps:
        msgs.append("heat gaps >0.6pp: " + ", ".join(gaps))
    return doc, " | ".join(msgs) if msgs else "heat ok"


def apply_es_euribor(ed, mk, log):
    """Spain box: the Euribor 12M row from the daily feed (EMMI fixing, one-day delay, plus the ECB monthly average)."""
    f = mk.get("esEuribor")
    es = next((c for c in ed.get("macro", []) if c.get("c") == "ES"), None)
    if not es:
        return
    row = next((r for r in es.get("rows", []) if r.get("k", "").startswith("Euribor 12M")), None)
    if not f or not row:
        log.append("ES EURIBOR 12M FEED MISSING (check the GitHub run): update the Euribor 12M row by hand" if not f
                   else "ES box has no 'Euribor 12M' row: feed not applied")
        return
    old = (row.get("v"), row.get("p"))
    row["v"], row["p"] = f"{f['v']:.3f}%", f"fix {short(f['d'])}"
    bits = []
    if f.get("mAvg"):
        bits.append(f"{MON[int(f['mAvg']['m'][5:7]) - 1]} avg {f['mAvg']['v']:.3f}%")
    ref = f.get("wk") or f.get("prev")
    if ref:
        bits.append(f"{(f['v'] - ref['v']) * 100:+.1f}bp vs {short(ref['d'])}")
    row["note"] = "; ".join(bits) if bits else row.get("note", "")
    src = es.setdefault("src", [])
    item = {"n": f"{f['src']}: 12M Euribor daily fixings (EMMI)", "u": f["url"]}
    i = next((j for j, x in enumerate(src) if "euribor" in (x.get("n", "") + x.get("u", "")).lower()), None)
    if i is None:
        src.append(item)
    else:
        src[i] = item
    log.append(f"ES EURIBOR 12M SET FROM FEED: {old[0]} [{old[1]}] -> {row['v']} [{row['p']}] ({row['note']}); leave this row out of the patch")


def prep(a):
    prev = load(a.prev)
    ed = copy.deepcopy(prev)
    ed["date"] = a.date
    log = []
    mk = {}
    if a.markets:
        try:
            mk = load(a.markets)
            gen = dt.datetime.strptime(mk["generatedAt"], "%Y-%m-%dT%H:%MZ")
            if gen.date() != dt.datetime.now(dt.timezone.utc).date():
                log.append(f"MARKETS FILE STALE (built {mk['generatedAt']}, not today UTC): the scheduled run "
                           "has not happened yet; run the heatmap-data workflow manually or use the fallback sources")
                mk = {}
            if mk.get("errors"):
                log.append("markets.json errors: " + "; ".join(mk["errors"]))
        except Exception as e:
            log.append(f"MARKETS FILE UNREADABLE ({e}): use the fallback sources")
            mk = {}
    if mk and mk.get("mxCetes"):  # Banxico SIE feed: use instead of searching for the Cetes auction
        mc = mk["mxCetes"]
        log.append("MX Cetes (Banxico SIE) latest: " + json.dumps(mc.get("latest")) +
                   " | look-backs: " + json.dumps(mc.get("hist")) + " | series: " + json.dumps(mc.get("titles")))
    D = apply_tables(ed, mk, log) if mk else None
    Dc = apply_us_curve(ed, mk, log) if mk else None
    if mk:
        apply_us_infl(ed, mk, log)
        apply_ez(ed, mk, log)
        apply_es_euribor(ed, mk, log)
    D = D or Dc
    before = len(ed.get("calendar", []))
    ed["calendar"] = sorted([c for c in ed.get("calendar", []) if c.get("d", "") >= a.date], key=lambda c: c["d"])
    log.append(f"calendar: dropped {before - len(ed['calendar'])} past events, {len(ed['calendar'])} left")
    heat = None
    if a.heat:
        try:
            heat = load(a.heat)
        except Exception as e:
            log.append(f"heat file unreadable: {e}")
    hdoc, hmsg = build_heat(ed, heat, a.date, mk)
    log.append(hmsg)
    dump(ed, f"{a.out}/base.json", pretty=True)
    if hdoc:
        dump(hdoc, f"{a.out}/heat.json")
    # ---- compact summary for the run
    out = [f"BASE for {a.date} written; latest market close D = {D}", ""]
    out += ["LOG:"] + ["- " + x for x in log] + [""]
    out.append("MARKETS: " + "; ".join(f"{r['n']} {r['lvl']} ({r['d1']}%)" for r in ed["indices"] + ed["fx"] + ed["cmd"]))
    for c in ed["macro"]:
        cb = c.get("cb") or {}
        out.append(f"MACRO {c['c']}: cb {cb.get('rate')} next {cb.get('next')} | " +
                   "; ".join(f"{r['k']}={r['v']} [{r.get('p', '')}]" for r in c["rows"]) +
                   f" | curve {c.get('curveAsOf')} | infl {c['infl']['asOf'] if c.get('infl') else '-'}")
    out.append("CALENDAR: " + "; ".join(f"{c['d']} {c['r']} {c['e']}" for c in ed["calendar"]))
    out.append("YESTERDAY TOP: " + " | ".join(t["t"] for t in prev.get("top", [])))
    out.append("YESTERDAY SPAIN: " + " | ".join(t["t"] for t in prev.get("spain", [])))
    out.append("YESTERDAY MEXICO: " + " | ".join(t["t"] for t in prev.get("mexico", [])))
    out.append("YESTERDAY DEALS: " + " | ".join(t["t"] for t in prev.get("deals", [])))
    out.append("YESTERDAY IPOS: " + " | ".join(f"{t['n']} ({t.get('when', '')})" for t in prev.get("ipos", [])))
    out.append("OFFICIALS: " + "; ".join(f"{o['c']}: " + ", ".join(f"{r['role']} {r['name']}" for r in o["rows"]) for o in prev.get("officials", [])))
    print("\n".join(out))


# ---------------------------------------------------------------- merge
def merge(a):
    ed = load(a.base)
    patch = load(a.patch)
    meta = load(a.index)
    errs, warns = [], []
    for k in REQUIRED_PATCH:
        if k not in patch:
            errs.append(f"patch missing '{k}'")
    for k in ("tape", "dataAsOf", "generatedAt", "top", "calendar", "officials", "spain", "mexico", "deals", "ipos",
              "talk", "learn", "quiz", "factoid", "notes", "sources", "marketsNote", "fxNote"):
        if k in patch:
            ed[k] = patch[k]
    for name, vals in (patch.get("markets") or {}).items():
        hit = [r for t in ("indices", "fx", "cmd") for r in ed[t] if r["n"] == name]
        if not hit:
            errs.append(f"patch.markets: unknown row {name}")
        for r in hit:
            r.update(vals)
    for c, part in (patch.get("macro") or {}).items():
        tgt = next((x for x in ed["macro"] if x["c"] == c), None)
        if not tgt:
            errs.append(f"patch.macro: unknown country {c}")
            continue
        tgt.update(part)
    ed["calendar"] = sorted([c for c in ed["calendar"] if c.get("d", "") >= ed["date"]], key=lambda c: c["d"])

    # ---- quality checks
    if sorted(ed.keys()) != sorted(TEMPLATE_KEYS):
        errs.append(f"top-level keys differ: {sorted(set(ed) ^ set(TEMPLATE_KEYS))}")
    txt = json.dumps(ed, ensure_ascii=False)
    for ch in ("—", "–"):
        if ch in txt:
            i = txt.index(ch)
            errs.append(f"dash {ch!r} found near: {txt[max(0, i - 40):i + 20]}")
    if len(txt.encode()) > 80 * 1024:
        errs.append(f"edition is {len(txt.encode()) // 1024} KB (limit 80)")
    if not 7 <= len(ed["top"]) <= 10:
        errs.append(f"top has {len(ed['top'])} items (7-10)")
    for i, t in enumerate(ed["top"]):
        miss = [k for k in ("tag", "t", "s", "why", "src") if not t.get(k)]
        if miss:
            errs.append(f"top[{i}] missing {miss}")
    if not 15 <= len(ed["calendar"]) <= 22:
        warns.append(f"calendar has {len(ed['calendar'])} items (15-22)")
    for c in ed["calendar"]:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", c.get("d", "")) or c.get("k") not in ("data", "cb", "election", "ipo", "earnings", "policy"):
            errs.append(f"calendar item invalid: {c}")
    for tbl in ("indices", "fx", "cmd"):
        for r in ed[tbl]:
            for k in KEYS:
                if r.get(k) is not None and not isinstance(r[k], (int, float)):
                    errs.append(f"{tbl} {r['n']} {k} is not a number")
    for c in ed["macro"]:
        inf = c.get("infl")
        if inf:
            ms = [[p["m"] for p in s["pts"]] for s in inf["series"]]
            if any(m != ms[0] for m in ms) or len(ms[0]) != 13:
                errs.append(f"macro {c['c']} infl series months differ or not 13")
        tn = [p["t"] for p in c.get("curve", [])]
        for h in c.get("curveHist", []):
            if [p["t"] for p in h["pts"]] != tn:
                errs.append(f"macro {c['c']} curveHist {h['k']} tenors differ from curve")
    q = ed["quiz"]
    if len(q) != 3 or any(len(x.get("opts", [])) != 4 or x.get("a") not in (0, 1, 2, 3) for x in q):
        errs.append("quiz must be 3 questions with 4 options and a in 0-3")
    if len({x.get("a") for x in q}) == 1:
        warns.append("all quiz answers in the same position")
    L = ed["learn"]
    for line in L.get("formula", []) + L.get("example", []):
        if len(line) >= 45:
            errs.append(f"learn line >= 45 chars: {line}")
    topics = {x.get("topic", "").strip().lower() for x in meta.get("lessons", [])}
    if L.get("topic", "").strip().lower() in topics:
        errs.append(f"lesson topic already used: {L.get('topic')}")
    facts = {x.get("t", "").strip().lower() for x in meta.get("factoids", [])}
    if patch.get("factoidShort", "").strip().lower() in facts:
        errs.append("factoid already used")
    if len(patch.get("tapeShort", "")) >= 120:
        errs.append("tapeShort must be under 120 characters")

    # ---- outputs
    date = ed["date"]
    wd = dt.date.fromisoformat(date).strftime("%a")
    meta = copy.deepcopy(meta)
    meta["editions"] = [{"date": date, "weekday": wd, "tape": patch.get("tapeShort", "")}] + \
                       [e for e in meta.get("editions", []) if e.get("date") != date]
    meta["latest"] = date
    meta["lessons"] = [x for x in meta.get("lessons", []) if x.get("date") != date] + \
                      [{"date": date, "track": L.get("track"), "topic": L.get("topic")}]
    meta["factoids"] = [x for x in meta.get("factoids", []) if x.get("date") != date] + \
                       [{"date": date, "t": patch.get("factoidShort", "")}]
    us = next((c for c in ed["macro"] if c["c"] == "US"), {})
    m = re.search(r"(\d{1,2}) ([A-Z][a-z]{2})", us.get("curveAsOf", ""))
    D = None
    if m:
        mon = MON.index(m.group(2)) + 1
        yr = int(date[:4]) - (1 if mon > int(date[5:7]) else 0)
        D = f"{yr}-{mon:02d}-{int(m.group(1)):02d}"
    curves = {"date": D, "src": "US: Treasury par curve; EZ (Bund), ES, UK: Trading Economics; MX: Banxico Cetes auctions and TE 10Y"}
    for c in ed["macro"]:
        curves[c["c"]] = c.get("curve", [])
    dump(ed, f"{a.out}/edition.json", pretty=True)
    dump(meta, f"{a.out}/index.json", pretty=True)
    dump(curves, f"{a.out}/curves.json", pretty=True)
    print(f"edition {date}: {len(txt.encode()) // 1024} KB; curves date {D}")
    for w in warns:
        print("WARN", w)
    for e in errs:
        print("ERROR", e)
    if errs:
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("prep")
    p1.add_argument("--prev", required=True)
    p1.add_argument("--date", required=True)
    p1.add_argument("--out", required=True)
    p1.add_argument("--markets")
    p1.add_argument("--heat")
    p2 = sub.add_parser("merge")
    p2.add_argument("--base", required=True)
    p2.add_argument("--patch", required=True)
    p2.add_argument("--index", required=True)
    p2.add_argument("--out", required=True)
    a = ap.parse_args()
    prep(a) if a.cmd == "prep" else merge(a)
