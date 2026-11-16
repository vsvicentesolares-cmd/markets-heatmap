"""Economic Cycle Compass feed. Runs in GitHub Actions (open internet), writes compass/feed.json.

Each series is {key, mode, data: {period: value}, source, last}; compass_tools.py applies them over the frozen
inputs in raw/. A source that fails is logged in "log" and skipped: the previous values stay in place.
Modes: replace (official series, revisions welcome), fill (market series: only new periods, history kept),
chain (extend a level series past its last period with this source's growth rates).
"""
import datetime as dt, io, json, math, os, re, sys, time
import requests
import pandas as pd

UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) compass-feed/1.0 (github.com/vsvicentesolares-cmd/markets-heatmap)'}
START = '2018-01-01'
OUT = {'generated': dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%MZ'), 'series': [], 'log': []}
CUR = dt.date.today().strftime('%Y-%m')          # month in progress: market series stop before it


def log(msg):
    print(msg, file=sys.stderr); OUT['log'].append(msg)


def add(key, data, source, mode='replace', nd=4):
    data = {k: round(float(v), nd) for k, v in data.items() if v is not None and not (isinstance(v, float) and math.isnan(v))}
    if not data:
        log(f'{key}: empty from {source}'); return
    OUT['series'].append({'key': key, 'mode': mode, 'source': source, 'last': max(data), 'data': dict(sorted(data.items()))})


def get(url, params=None, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=90)
            r.raise_for_status(); return r
        except Exception as e:
            if i == tries - 1: raise
            time.sleep(5 * (i + 1))


def safe(name):
    def deco(fn):
        def run():
            try: fn()
            except Exception as e: log(f'{name}: {type(e).__name__}: {str(e)[:200]}')
        return run
    return deco


# ---------------------------------------------------------------- sources
def fred(sid, start=START, avg=False, quarterly=False):
    url = f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}' + ('&fq=Monthly&fam=avg' if avg else '')
    df = pd.read_csv(io.StringIO(get(url).text))
    vals = pd.to_numeric(df.iloc[:, 1], errors='coerce')
    return {str(d)[:7]: float(v) for d, v in zip(df.iloc[:, 0], vals) if pd.notna(v)}


def eurostat(ds, params):
    """Eurostat JSON-stat 2.0, one series (every dimension but time fixed by params)."""
    url = f'https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{ds}'
    j = get(url, params=params + [('format', 'JSON'), ('lang', 'EN')]).json()
    ids, sizes = j['id'], j['size']
    if any(s != 1 for i, s in zip(ids, sizes) if i != 'time'):
        raise ValueError(f'{ds}: more than one series {dict(zip(ids, sizes))}')
    tpos = {v: k for k, v in j['dimension']['time']['category']['index'].items()}
    out = {}
    for idx, v in j.get('value', {}).items():
        p = tpos[int(idx)]
        if '-Q' in p: y, q = p.split('-Q'); p = f'{y}-{(int(q) - 1) * 3 + 1:02d}'
        out[p] = float(v)
    return out


def ecb(key):
    df = pd.read_csv(io.StringIO(get(f'https://data-api.ecb.europa.eu/service/data/{key}', params={'format': 'csvdata', 'startPeriod': '2018-01'}).text))
    return {str(p)[:7]: float(v) for p, v in zip(df['TIME_PERIOD'], df['OBS_VALUE']) if pd.notna(v)}


def ons(path, kind):
    """ONS time series JSON: kind 'months' or 'quarters'."""
    j = get(f'https://www.ons.gov.uk/{path}/data').json()
    mon = {m: i + 1 for i, m in enumerate('JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC'.split())}
    out = {}
    for o in j.get(kind, []):
        d, v = o['date'], o['value']
        if v in ('', None): continue
        if kind == 'months': y, m = d.split(); out[f'{y}-{mon[m]:02d}'] = float(v)
        else: y, q = d.split(); out[f'{y}-{(int(q[1]) - 1) * 3 + 1:02d}'] = float(v)
    return out


_YF = {}
def yf_daily(t):
    if t not in _YF:
        import yfinance as yf
        df = yf.download(t, start='2018-11-01', interval='1d', auto_adjust=False, progress=False)
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        _YF[t] = df
    return _YF[t]


def yf_month(t, how='last', col='Close'):
    s = yf_daily(t)[col].dropna()
    g = s.groupby(s.index.to_period('M'))
    s = g.last() if how == 'last' else g.mean()
    return {str(p): float(v) for p, v in s.items() if str(p) < CUR}


def worldperatio(slug):
    html = get(f'https://worldperatio.com/area/{slug}/').text
    m = re.search(r'detailPE_data\s*=\s*\[(.*?)\];', html, re.S)
    if not m: raise ValueError('detailPE_data not found')
    pts = re.findall(r'Date\.UTC\((\d+),\s*(\d+),\s*\d+\)\s*,\s*([\d.]+)\]', m.group(1))
    return {f'{y}-{int(mo) + 1:02d}': float(v) for y, mo, v in pts if int(y) >= 2018}


def multpl(path):
    html = get(f'https://www.multpl.com/{path}/table/by-month').text
    df = pd.read_html(io.StringIO(html))[0]
    out = {}
    for d, v in zip(df.iloc[:, 0], df.iloc[:, 1]):
        try: p = pd.to_datetime(d).strftime('%Y-%m')
        except Exception: continue
        if p < '2018-01': continue
        s = str(v); est = ('estimate' in s.lower()) or ('†' in s)
        n = re.sub(r'[^\d.\-]', '', s.replace('estimate', ''))
        if n: out[p] = (float(n), est)
    return out


def ameco(var, codes):
    url = 'https://ec.europa.eu/economy_finance/ameco/wq/series'
    years = ','.join(str(y) for y in range(2018, dt.date.today().year + 3))
    html = get(url, params={'fullVariable': f'1.0.0.0.{var}', 'countries': ','.join(codes), 'years': years, 'Lastyear': '0', 'Yearorder': 'ASC'}).text
    df = pd.read_html(io.StringIO(html))[0]
    out = {}
    for _, r in df.iterrows():
        name = str(r.iloc[0])
        out[name] = {str(c): float(r[c]) for c in df.columns if re.fullmatch(r'\d{4}', str(c)) and pd.notna(pd.to_numeric(r[c], errors='coerce'))}
    return out


# ---------------------------------------------------------------- series
@safe('us_fred')
def us():
    for f, col, sid, q in [('us_rates.csv', 'UNRATE', 'UNRATE', 0), ('us_rates.csv', 'FEDFUNDS', 'FEDFUNDS', 0), ('us_rates.csv', 'GS10', 'GS10', 0),
                           ('us_rates.csv', 'GS2', 'GS2', 0), ('us_prices.csv', 'CPIAUCSL', 'CPIAUCSL', 0), ('us_prices.csv', 'PCEPILFE', 'PCEPILFE', 0),
                           ('us_prices.csv', 'PALLFNFINDEXM', 'PALLFNFINDEXM', 0), ('us_gdp_q.csv', 'GDPC1', 'GDPC1', 1),
                           ('us_gdp_q.csv', 'GDPPOT', 'GDPPOT', 1), ('us_gdp_q.csv', 'NROU', 'NROU', 1)]:
        try: add(f'{f}:{col}', fred(sid, '2019-01-01'), f'FRED {sid}')
        except Exception as e: log(f'FRED {sid}: {e!r}'[:200])


@safe('us_market')
def us_market():
    add('shiller_sp500.csv:SP500', yf_month('^GSPC', 'mean'), 'Yahoo ^GSPC monthly average close', 'fill', 2)
    pe = multpl('s-p-500-pe-ratio'); eps = multpl('s-p-500-earnings'); dy = multpl('s-p-500-dividend-yield')
    add('us_sp500_eps_pe.csv:pe_ttm', {p: v for p, (v, e) in pe.items()}, 'multpl S&P 500 P/E by month', 'replace', 2)
    add('us_sp500_eps_pe.csv:pe_estimate', {p: 1 if e else 0 for p, (v, e) in pe.items()}, 'multpl estimate flag', 'replace', 0)
    add('us_sp500_eps_pe.csv:eps_ttm', {p: v for p, (v, e) in eps.items() if not e}, 'multpl S&P 500 TTM earnings', 'replace', 2)
    add('us_divyield.csv:dy', {p: v for p, (v, e) in dy.items()}, 'multpl S&P 500 dividend yield', 'replace', 2)


@safe('es')
def es():
    add('es_inputs.URATE', fred('LRHUTTTTESM156S', '2019-01-01'), 'FRED LRHUTTTTESM156S')
    add('es_inputs.URATE', eurostat('une_rt_m', [('geo', 'ES'), ('s_adj', 'SA'), ('age', 'TOTAL'), ('sex', 'T'), ('unit', 'PC_ACT'), ('sinceTimePeriod', '2019-01')]), 'Eurostat une_rt_m ES', 'fill')
    add('es_inputs.Y10', fred('IRLTLT01ESM156N', '2019-01-01'), 'FRED IRLTLT01ESM156N', nd=3)
    add('es_inputs.DEPO', fred('ECBDFR', '2019-01-01', avg=True), 'FRED ECBDFR monthly average', nd=2)
    add('es_inputs.EUR3M', ecb('FM/M.U2.EUR.RT.MM.EURIBOR3MD_.HSTA'), 'ECB 3M Euribor monthly average', nd=3)
    add('es_inputs.CORE', eurostat('prc_hicp_minr', [('geo', 'ES'), ('unit', 'RCH_A'), ('coicop18', 'TOT_X_NRG_FOOD'), ('sinceTimePeriod', '2026-01')]), 'Eurostat prc_hicp_minr ES core', nd=1)
    add('es_inputs.GDPQ', fred('CLVMNACSCAB1GQES', '2018-01-01'), 'FRED CLVMNACSCAB1GQES', nd=1)
    add('es_inputs.GDPQ', eurostat('namq_10_gdp', [('geo', 'ES'), ('unit', 'CLV10_MEUR'), ('s_adj', 'SCA'), ('na_item', 'B1GQ'), ('sinceTimePeriod', '2024-Q1')]), 'Eurostat namq_10_gdp ES (growth)', 'chain', 1)
    add('es_inputs.IBEX', yf_month('^IBEX'), 'Yahoo ^IBEX month-end close', 'fill', 1)


@safe('mx')
def mx():
    add('mx_inputs.URATE', fred('LRHUTTTTMXM156S', '2005-01-01'), 'FRED LRHUTTTTMXM156S', nd=3)
    add('mx_inputs.Y10', fred('IRLTLT01MXM156N', '2019-01-01'), 'FRED IRLTLT01MXM156N', nd=3)
    add('mx_inputs.TIIE3M', fred('IR3TIB01MXM156N', '2019-01-01'), 'FRED IR3TIB01MXM156N', nd=3)
    add('mx_inputs.GDPQ', fred('NGDPRSAXDCMXQ', '2005-01-01'), 'FRED NGDPRSAXDCMXQ', nd=1)
    add('mx_inputs.IPC', yf_month('^MXX'), 'Yahoo ^MXX month-end close', 'fill', 2)


@safe('uk')
def uk():
    add('uk_inputs.URATE', fred('LRHUTTTTGBM156S', '2019-01-01'), 'FRED LRHUTTTTGBM156S', nd=2)
    try: add('uk_inputs.URATE', ons('employmentandlabourmarket/peoplenotinwork/unemployment/timeseries/mgsx/lms', 'months'), 'ONS MGSX', 'fill', 1)
    except Exception as e: log(f'ONS MGSX: {e!r}'[:200])
    add('uk_inputs.Y10', fred('IRLTLT01GBM156N', '2019-01-01'), 'FRED IRLTLT01GBM156N')
    add('uk_inputs.GDPQ', fred('NGDPRSAXDCGBQ', '2018-01-01'), 'FRED NGDPRSAXDCGBQ', nd=1)
    try: add('uk_inputs.GDPQ', ons('economy/grossdomesticproductgdp/timeseries/abmi/qna', 'quarters'), 'ONS ABMI (growth)', 'chain', 1)
    except Exception as e: log(f'ONS ABMI: {e!r}'[:200])
    try:
        url = ('https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes&Datefrom=01/Jan/2019'
               '&Dateto=now&SeriesCodes=IUDBEDR&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N')
        df = pd.read_csv(io.StringIO(get(url).text))
        df['p'] = pd.to_datetime(df.iloc[:, 0], format='%d %b %Y').dt.strftime('%Y-%m')
        add('uk_inputs.BR', {p: float(g.iloc[-1, 1]) for p, g in df.groupby('p') if p < CUR}, 'Bank of England IUDBEDR month end', nd=2)
    except Exception as e: log(f'BoE Bank Rate: {e!r}'[:200])
    add('pe_inputs.FTSE', yf_month('^FTSE'), 'Yahoo ^FTSE month-end close', 'fill', 2)
    tr = yf_month('ISF.L', 'last', 'Adj Close'); ks = sorted(tr)
    add('uk_inputs.EQ_TR', {b: (tr[b] / tr[a] - 1) * 100 for a, b in zip(ks, ks[1:])}, 'Yahoo ISF.L adjusted close, monthly % change', 'fill', 1)


@safe('ez')
def ez():
    add('ez_inputs.URATE', eurostat('une_rt_m', [('geo', 'EA21'), ('s_adj', 'SA'), ('age', 'TOTAL'), ('sex', 'T'), ('unit', 'PC_ACT'), ('sinceTimePeriod', '2018-10')]), 'Eurostat une_rt_m EA21', nd=1)
    for k, c in (('CPI', 'TOTAL'), ('CORE', 'TOT_X_NRG_FOOD')):
        add(f'ez_inputs.{k}', eurostat('prc_hicp_minr', [('geo', 'EA'), ('unit', 'RCH_A'), ('coicop18', c), ('sinceTimePeriod', '2026-01')]), f'Eurostat prc_hicp_minr EA {c}', nd=1)
    for k, mat in (('Y2', 'Y2'), ('Y10', 'Y10')):
        add(f'ez_inputs.{k}', eurostat('irt_euryld_m', [('yld_curv', 'SPOT_RT'), ('bonds', 'CGB_EA'), ('maturity', mat), ('sinceTimePeriod', '2018-10')]), f'Eurostat irt_euryld_m CGB_EA {mat}', nd=5)
    add('ez_inputs.GDPQ', eurostat('namq_10_gdp', [('geo', 'EA20'), ('unit', 'CLV10_MEUR'), ('s_adj', 'SCA'), ('na_item', 'B1GQ'), ('sinceTimePeriod', '2018-Q1')]), 'Eurostat namq_10_gdp EA20', nd=1)
    for k, sid in (('IT10', 'IRLTLT01ITM156N'), ('FR10', 'IRLTLT01FRM156N'), ('DE10', 'IRLTLT01DEM156N')):
        add(f'ez_inputs.{k}', fred(sid, '2019-01-01'), f'FRED {sid}')
    add('ez_inputs.SX5E', yf_month('^STOXX50E'), 'Yahoo ^STOXX50E month-end close', 'fill', 2)


@safe('pe')
def pe():
    for key, slug in (('pe_inputs.PE_ES', 'spain'), ('pe_inputs.PE_MX', 'mexico'), ('pe_inputs.PE_UK', 'united-kingdom'),
                      ('ez_inputs.PE_DE', 'germany'), ('ez_inputs.PE_FR', 'france'), ('ez_inputs.PE_NL', 'netherlands'), ('ez_inputs.PE_IT', 'italy')):
        try: add(key, worldperatio(slug), f'worldperatio.com/area/{slug} ETF P/E')
        except Exception as e: log(f'worldperatio {slug}: {e!r}'[:200])


@safe('ameco')
def amc():
    for var, key in (('AVGDGP', 'AMECO_GAP'), ('ZNAWRU', 'AMECO_NAWRU'), ('OVGD', 'AMECO_OVGD')):
        try: t = ameco(var, ['ESP', 'GBR', 'EA20'])
        except Exception as e: log(f'AMECO {var}: {e!r}'[:200]); continue
        for name, mod in (('Spain', 'es_inputs'), ('United Kingdom', 'uk_inputs'), ('Euro area', 'ez_inputs')):
            if mod == 'ez_inputs' and var == 'OVGD': continue
            row = next((v for n, v in t.items() if n.startswith(name)), None)
            if row: add(f'{mod}.{key}', row, f'AMECO {var}', nd=5)
            else: log(f'AMECO {var}: no row for {name}')


if __name__ == '__main__':
    for fn in (us, us_market, es, mx, uk, ez, pe, amc): fn()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'feed.json')
    with open(path, 'w') as f: json.dump(OUT, f, separators=(',', ':'))
    print(f"feed: {len(OUT['series'])} series, {len(OUT['log'])} log lines -> {path}")
