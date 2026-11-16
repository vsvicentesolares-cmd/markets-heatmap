"""Economic Cycle Compass: rebuild the monthly data from frozen inputs + the GitHub feed + manual entries.

  python3 compass_tools.py prep  --manual manual.json [--end YYYY-MM]
  python3 compass_tools.py build --manual manual.json [--end YYYY-MM] --out OUTDIR

Layers, applied in this order:
  1. raw/            frozen inputs as built by hand (2019 to Oct 2026)
  2. feed.json       written by the GitHub workflow (fetch_compass.py): API series, each with a mode
                     replace = overwrite those periods, fill = only periods still empty,
                     chain = extend past the last period using the feed series' growth rates
                     splice = extend past the last period adding the feed series' changes (rates, e.g. unemployment)
  3. manual.json     {"series": {key: {period: value}}, "src": {key: text}, "override": [keys]}
                     kept in the artifact's database (inputs/manual); fills gaps only, unless the key is in "override"
                     (CDS keys always overwrite, so a month-to-date close can be replaced by the month-end close)
Keys: "<module>.<VAR>" for raw/*_inputs.py dicts (es_inputs, mx_inputs, uk_inputs, ez_inputs, pe_inputs),
      "<file>.csv:<column>" for the US csv inputs, "cds.<US|ES|UK|MX>" for CDS month-end closes.
Periods are YYYY-MM (quarters by their first month, e.g. 2026-07 for Q3); AMECO dicts are keyed by year (YYYY).
"""
import argparse, datetime as dt, importlib, json, math, os, runpy, shutil, subprocess, sys, csv

HERE = os.path.dirname(os.path.abspath(__file__))
MODS = ['es_inputs', 'mx_inputs', 'uk_inputs', 'ez_inputs', 'pe_inputs']
CSV_FLAGS = {'pe_estimate', 'estimate'}
# what the scheduled run fills by hand, with where to look
MANUAL = [
    ('us_ism.csv:ISM_PMI', 'm', 'US ISM manufacturing PMI (ISM release, 1st business day)'),
    ('es_inputs.PMI', 'm', 'HCOB/S&P Global Spain manufacturing PMI, final'),
    ('mx_inputs.PMI', 'm', 'S&P Global Mexico manufacturing PMI'),
    ('uk_inputs.PMI', 'm', 'S&P Global UK manufacturing PMI, final'),
    ('ez_inputs.PMI', 'm', 'HCOB/S&P Global Eurozone manufacturing PMI, final'),
    ('es_inputs.CPI', 'm', 'INE IPC general annual rate (%), e.g. RateInflation Spain table; flash allowed, replace with final'),
    ('mx_inputs.CPI', 'm', 'INEGI INPC annual rate (%), RateInflation Mexico table'),
    ('mx_inputs.CORE', 'm', 'INEGI subyacente annual rate (%), INEGI INPC boletin'),
    ('mx_inputs.POLICY', 'm', 'Banxico target rate at month end (%)'),
    ('uk_inputs.CPI', 'm', 'OECD UK CPI all items annual rate (CPIH basis), OECD Data Explorer DF_PRICES_ALL'),
    ('uk_inputs.CORE', 'm', 'OECD UK CPI excl. food and energy annual rate (CPIH basis)'),
    ('ez_inputs.CPI', 'm', 'Euro area HICP annual rate (Eurostat flash if the feed lacks it)'),
    ('ez_inputs.CORE', 'm', 'Euro area HICP excl. energy, food, alcohol, tobacco (flash if missing)'),
    ('mx_inputs.GDPQ', 'q', 'Mexico real GDP SA level: previous quarter x (1 + INEGI flash q/q)'),
    ('uk_inputs.GDPQ', 'q', 'UK real GDP level: previous quarter x (1 + ONS first estimate q/q)'),
    ('es_inputs.GDPQ', 'q', 'Spain real GDP level: previous quarter x (1 + INE flash q/q)'),
    ('ez_inputs.GDPQ', 'q', 'Euro area real GDP level: previous quarter x (1 + Eurostat flash q/q)'),
    ('cds.US', 'm', 'US 5Y CDS monthly close (Investing.com historical data, monthly)'),
    ('cds.ES', 'm', 'Spain 5Y CDS monthly close'),
    ('cds.UK', 'm', 'UK 5Y CDS monthly close'),
    ('cds.MX', 'm', 'Mexico 5Y CDS monthly close'),
]


def prev_month(today=None):
    t = today or dt.date.today()
    y, m = t.year, t.month - 1
    if m == 0: y, m = y - 1, 12
    return f'{y}-{m:02d}'


def shift(p, n):
    y, m = map(int, p.split('-')); t = y * 12 + m - 1 + n
    return f'{t // 12}-{t % 12 + 1:02d}'


def keyconv(d, sample):
    """AMECO dicts use int years; everything else YYYY-MM strings."""
    if sample is not None and isinstance(sample, int):
        return {int(k): v for k, v in d.items()}
    return dict(d)


class Store:
    def __init__(self, workdir):
        self.dir = workdir
        sys.path.insert(0, os.path.join(workdir, 'raw'))
        for m in MODS: sys.modules.pop(m, None)
        self.mod = {m: importlib.import_module(m) for m in MODS}
        self.csv = {}
        self.cds = {}
        mon = {m: i + 1 for i, m in enumerate('Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split())}
        for c in ['US', 'ES', 'UK', 'MX']:          # month-end closes read by hand so far (raw/cds_*.txt)
            for line in open(os.path.join(workdir, 'raw', f'cds_{c.lower()}.txt')):
                p = line.split()
                if len(p) >= 4: self.cds.setdefault(c, {})[f'{p[2]}-{mon[p[0]]:02d}'] = float(p[3])
        self.log = []

    # --- access -------------------------------------------------------------------------------
    def series(self, key):
        if key.startswith('cds.'):
            return self.cds.setdefault(key[4:], {})
        if '.csv:' in key:
            f, col = key.split(':')
            rows = self._csv(f)
            return {d: (float(r[col]) if r.get(col) not in (None, '') else None) for d, r in rows.items() if col in r}
        m, var = key.split('.', 1)
        return getattr(self.mod[m], var)

    def _csv(self, f):
        if f not in self.csv:
            path = os.path.join(self.dir, 'raw', f)
            with open(path) as fh:
                rd = list(csv.DictReader(fh))
            datecol = 'Date' if 'Date' in rd[0] else 'date'
            self.csv[f] = {'_datecol': datecol, '_cols': list(rd[0].keys()), 'rows': {r[datecol][:7]: r for r in rd}}
        return self.csv[f]['rows']

    def set(self, key, period, value):
        if key.startswith('cds.'):
            self.cds.setdefault(key[4:], {})[period] = value; return
        if '.csv:' in key:
            f, col = key.split(':')
            rows = self._csv(f); meta = self.csv[f]
            if period not in rows:
                r = {c: '' for c in meta['_cols']}
                r[meta['_datecol']] = period + ('-01' if meta['_datecol'] == 'Date' else '')
                rows[period] = r
            if col in CSV_FLAGS:
                rows[period][col] = '1' if value else ''
            else:
                rows[period][col] = '' if value is None else repr(float(value))
            return
        d = self.series(key)
        sample = next(iter(d), None)
        if isinstance(sample, int): period = int(period)
        d[period] = value

    def get(self, key, period):
        d = self.series(key)
        sample = next(iter(d), None)
        if isinstance(sample, int): period = int(period)
        return d.get(period)

    def periods(self, key):
        d = self.series(key)
        return sorted(str(k) for k, v in d.items() if v is not None)

    # --- layers -------------------------------------------------------------------------------
    def apply(self, key, data, mode='replace', src=''):
        n = 0
        if mode == 'chain':
            have = self.periods(key)
            common = [p for p in have if p in data and data[p]]
            if not common:
                self.log.append(f'chain {key}: no common period, skipped'); return 0
            last = common[-1]; base = self.get(key, last)
            for p in sorted(data):
                if p <= last or data.get(p) is None: continue
                base = base * data[p] / data[last]; last = p
                if self.get(key, p) is None or p > have[-1]:
                    self.set(key, p, round(base, 1)); n += 1
            return n
        if mode == 'splice':
            # add the source's changes onto the last period we already have (avoids level breaks between sources)
            have = self.periods(key)
            if not have or have[-1] not in data:
                self.log.append(f'splice {key}: no overlap at {have[-1] if have else None}, skipped'); return 0
            last = have[-1]; base = self.get(key, last)
            for p in sorted(data):
                if p > last and data[p] is not None:
                    self.set(key, p, round(base + data[p] - data[last], 2)); n += 1
            return n
        for p, v in data.items():
            if v is None: continue
            if mode == 'fill' and self.get(key, p) is not None: continue
            if mode == 'replace' or self.get(key, p) != v:
                self.set(key, p, v); n += 1
        return n

    def finish(self):
        """Rules that depend on several series."""
        uk = self.mod['uk_inputs']
        for p, v in uk.BR.items():          # 3M interbank stands in from Bank Rate after the FRED series ended (Jan 2026)
            if p > '2026-01' and uk.I3M.get(p) is None and v is not None: uk.I3M[p] = v

    def write_csvs(self):
        for f, meta in self.csv.items():
            rows = [meta['rows'][k] for k in sorted(meta['rows'])]
            with open(os.path.join(self.dir, 'raw', f), 'w', newline='') as fh:
                w = csv.DictWriter(fh, fieldnames=meta['_cols']); w.writeheader(); w.writerows(rows)
        with open(os.path.join(self.dir, 'cds_overlay.json'), 'w') as fh:
            json.dump(self.cds, fh)


def load_layers(store, feed, manual):
    stats = []
    for s in feed.get('series', []):
        try:
            n = store.apply(s['key'], s['data'], s.get('mode', 'replace'))
            stats.append((s['key'], s.get('mode', 'replace'), n, s.get('last')))
        except Exception as e:  # a feed series that does not fit is reported, never fatal
            store.log.append(f"feed {s.get('key')}: {e!r}")
    over = set(manual.get('override', []))
    for key, data in manual.get('series', {}).items():
        try:
            store.apply(key, data, 'replace' if key in over or key.startswith('cds.') else 'fill')
        except Exception as e:
            store.log.append(f'manual {key}: {e!r}')
    store.finish()
    return stats


def workdir_copy():
    w = '/tmp/compass_build'
    shutil.rmtree(w, ignore_errors=True)
    shutil.copytree(HERE, w, ignore=shutil.ignore_patterns('__pycache__', 'out', '.git'))
    return w


def read_json(p, default):
    return json.load(open(p)) if p and os.path.exists(p) else default


def todo(store, end):
    out = []
    for key, freq, hint in MANUAL:
        have = set(store.periods(key))
        if freq == 'm':
            want = [shift(end, -i) for i in (2, 1, 0)]
        else:
            q = shift(end, -((int(end[5:]) - 1) % 3))           # quarter containing END
            want = [shift(q, -3)]                               # last completed quarter by a mid-quarter run
        miss = [p for p in want if p not in have]
        last = max(have) if have else None
        lastv = f' [latest {last} = {store.get(key, last)}]' if freq == 'q' and last else ''
        if miss: out.append(f'{key:22s} missing {", ".join(miss):26s} {hint}{lastv}')
    return out


def cmd_prep(a):
    end = a.end or prev_month()
    w = workdir_copy(); st = Store(w)
    feed = read_json(a.feed, {}); man = read_json(a.manual, {})
    stats = load_layers(st, feed, man)
    print(f'TARGET END MONTH {end}   feed generated {feed.get("generated", "MISSING")}')
    print('FEED LOG:'); [print('  ' + l) for l in feed.get('log', [])] or print('  (none)')
    print('FEED SERIES (key, mode, periods changed, last period):')
    for k, mo, n, last in stats: print(f'  {k:28s} {mo:7s} {n:4d}  {last}')
    if st.log: print('APPLY LOG:'); [print('  ' + l) for l in st.log]
    print('MANUAL TODO (fill in manual.json "series", with the source in "src"):')
    t = todo(st, end); [print('  ' + l) for l in t] or print('  (nothing missing)')


def num_ok(v):
    return v is None or isinstance(v, (bool, str)) or (isinstance(v, (int, float)) and math.isfinite(v))


def cmd_build(a):
    end = a.end or prev_month()
    w = workdir_copy(); st = Store(w)
    feed = read_json(a.feed, {}); man = read_json(a.manual, {})
    load_layers(st, feed, man); st.write_csvs()
    os.chdir(w); os.environ['COMPASS_END'] = end
    for b in ['build_us.py', 'build_es.py', 'build_mx.py', 'build_uk.py', 'build_ez.py', 'build_cds.py']:
        print(f'--- {b}'); runpy.run_path(b, run_name='__main__')
    D = json.load(open('compass_monthly.json')); C = json.load(open('compass_cds.json'))
    snap = json.load(open(os.path.join(HERE, 'snapshot', 'compass_monthly.json')))
    errs, warns = [], list(st.log)
    months = []; p = '2019-01'
    while p <= end: months.append(p); p = shift(p, 1)
    for c in ['US', 'ES', 'MX', 'UK', 'EZ']:
        got = [r['month'] for r in D['rows'] if r['country'] == c]
        if got != months: errs.append(f'{c}: months {got[:1]}..{got[-1:]} ({len(got)}) expected 2019-01..{end} ({len(months)})')
    for r in D['rows'] + C['rows']:
        for k, v in r.items():
            if not num_ok(v): errs.append(f'{r["country"]} {r["month"]} {k} = {v}')
    old = {(r['country'], r['month']): r for r in snap['rows']}
    cut = shift(end, -12); rev = 0
    for r in D['rows']:
        o = old.get((r['country'], r['month']))
        if not o or r['month'] >= cut: continue
        for k, v in r.items():
            ov = o.get(k)
            if isinstance(v, (int, float)) and isinstance(ov, (int, float)) and not isinstance(v, bool):
                if abs(v - ov) > max(0.3, 0.02 * abs(ov)):
                    rev += 1
                    if rev <= 25: warns.append(f'revision {r["country"]} {r["month"]} {k}: {ov} -> {v}')
    if rev > 25: warns.append(f'... {rev} revisions in total older than {cut}')
    last = {c: [r for r in D['rows'] if r['country'] == c][-1] for c in ['US', 'ES', 'MX', 'UK', 'EZ']}
    for c, r in last.items():
        nulls = [k for k, v in r.items() if v is None]
        if nulls: warns.append(f'{c} {r["month"]} not published yet (carried by the engine): {", ".join(nulls)}')
    os.makedirs(a.out, exist_ok=True)
    shutil.copy('compass_monthly.json', os.path.join(a.out, 'compass_monthly.json'))
    shutil.copy('compass_cds.json', os.path.join(a.out, 'compass_cds.json'))
    print('WARNINGS:'); [print('  ' + l) for l in warns] or print('  (none)')
    print('PHASES:')
    prevf = a.previous or os.path.join(HERE, 'snapshot', 'compass_monthly.json')
    try:
        print(subprocess.run(['node', os.path.join(w, 'run_engine.mjs'), os.path.join(a.out, 'compass_monthly.json'), prevf],
                             capture_output=True, text=True, timeout=120).stdout)
    except Exception as e:
        print('  engine not run:', e)
    if errs:
        print('ERRORS:'); [print('  ' + l) for l in errs]; sys.exit(1)
    print(f'ALL CHECKS PASSED  ({len(D["rows"])} rows, {len(C["rows"])} cds rows, end {end}) -> {a.out}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['prep', 'build'])
    ap.add_argument('--manual'); ap.add_argument('--feed', default=os.path.join(HERE, 'feed.json'))
    ap.add_argument('--end'); ap.add_argument('--out', default='out'); ap.add_argument('--previous')
    a = ap.parse_args()
    a.out = os.path.abspath(a.out)
    {'prep': cmd_prep, 'build': cmd_build}[a.cmd](a)
