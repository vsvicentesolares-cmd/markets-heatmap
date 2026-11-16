"""Build the US monthly indicator table for the Economic Cycle Compass.
Inputs (raw/): FRED (UNRATE, FEDFUNDS, GS10, GS2, CPIAUCSL, PCEPILFE, PALLFNFINDEXM,
GDPC1, GDPPOT, NROU), ISM PMI (ISM releases, YCharts), multpl (S&P 500 EPS, P/E,
dividend yield, price), Shiller/datahub S&P 500 monthly average price."""
import csv, json, math, os
def rd(p):
    with open('raw/'+p) as f: return list(csv.DictReader(f))
def num(x):
    try: return float(x)
    except: return None
END=os.environ.get('COMPASS_END','2026-10'); YE=int(END[:4])+1
M=[f'{y}-{m:02d}' for y in range(2019,YE) for m in range(1,13) if f'{y}-{m:02d}'<=END]
rates={r['date']:r for r in rd('us_rates.csv')}
px={r['date']:r for r in rd('us_prices.csv')}
ism={r['date']:num(r['ISM_PMI']) for r in rd('us_ism.csv')}
q={r['date']:r for r in rd('us_gdp_q.csv')}
ep={r['date']:r for r in rd('us_sp500_eps_pe.csv')}
dy={}
for r in rd('us_divyield.csv'): dy[r['date']]=num(r['dy'])
spx={r['Date'][:7]:num(r['SP500']) for r in rd('shiller_sp500.csv')}
spx.setdefault('2026-10',7726.69)   # multpl monthly average, Oct 2026 (the feed's ^GSPC monthly mean replaces it)
def qkey(m):
    y,mo=m.split('-'); return f"{y}-{(int(mo)-1)//3*3+1:02d}"
def prevq(k,n):
    y,mo=map(int,k.split('-')); t=y*12+mo-1-3*n; return f"{t//12}-{t%12+1:02d}"
def prevm(m,n):
    y,mo=map(int,m.split('-')); t=y*12+mo-1-n; return f"{t//12}-{t%12+1:02d}"
def yoy(d,m,f):
    a=num(d.get(m,{}).get(f)); b=num(d.get(prevm(m,12),{}).get(f))
    return round((a/b-1)*100,2) if a and b else None
rows=[];bond=100.0;eq=100.0;base_cmd=None
for i,m in enumerate(M):
    qk=qkey(m); qr=q.get(qk,{})
    g=num(qr.get('GDPC1')); p=num(qr.get('GDPPOT')); g4=num(q.get(prevq(qk,4),{}).get('GDPC1'))
    g1=num(q.get(prevq(qk,1),{}).get('GDPC1'))
    gap=round((g/p-1)*100,2) if g and p else None
    gdp_yoy=round((g/g4-1)*100,2) if g and g4 else None
    gdp_saar=round(((g/g1)**4-1)*100,2) if g and g1 else None
    nrou=num(qr.get('NROU'))
    r=rates.get(m,{}); u=num(r.get('UNRATE')); ff=num(r.get('FEDFUNDS')); y10=num(r.get('GS10')); y2=num(r.get('GS2'))
    pce=yoy(px,m,'PCEPILFE'); cpi=yoy(px,m,'CPIAUCSL')
    taylor=round(2+pce+0.5*(pce-2)+0.5*gap,2) if pce is not None and gap is not None else None
    # 10Y Treasury total return: par bond bought at last month's yield, repriced at this month's
    if i>0 and y10 is not None and num(rates.get(M[i-1],{}).get('GS10')) is not None:
        c=num(rates[M[i-1]]['GS10'])/100; yy=y10/100; n=20-1/6
        price=sum((c/2)/(1+yy/2)**(k-1/6) for k in range(1,21))+1/(1+yy/2)**n
        bond*=price+c/12
    eps=num(ep.get(m,{}).get('eps_ttm')); pe=num(ep.get(m,{}).get('pe_ttm')); pe_est=ep.get(m,{}).get('pe_estimate')=='1'
    if eps is not None: last_eps=eps
    elif pe is not None: eps=last_eps  # latest reported TTM EPS carried forward; flagged via pe_estimate
    if i>0 and spx.get(m) and spx.get(M[i-1]):
        eq*=spx[m]/spx[M[i-1]]+(dy.get(M[i-1]) or 0)/1200
    cm=num(px.get(m,{}).get('PALLFNFINDEXM'))
    if m=='2020-01': base_cmd=cm
    rows.append(dict(country='US',month=m,
        output_gap=gap,gdp_yoy=gdp_yoy,gdp_saar=gdp_saar,ism=ism.get(m),urate=u,
        ugap=round(u-nrou,2) if u is not None and nrou is not None else None,
        cpi_yoy=cpi,core_pce_yoy=pce,policy_rate=ff,taylor=taylor,y10=y10,y2=y2,
        slope_bp=round((y10-y2)*100) if y10 is not None and y2 is not None else None,
        eps_ttm=eps,pe_ttm=pe,pe_estimate=pe_est,
        bond_tr=None,equity_tr=None,commodity_idx=None,_bond=bond,_eq=eq,_cm=cm))
# rebase indices to 100 at 2020-01
b0=dict([r for r in rows if r['month']=='2020-01'][0])
for r in rows:
    r['bond_tr']=round(r.pop('_bond')/b0['_bond']*100,2) if r['month']>='2019-02' else None
    r['equity_tr']=round(r.pop('_eq')/b0['_eq']*100,2) if r['month']>='2019-02' else None
    cm=r.pop('_cm'); r['commodity_idx']=round(cm/base_cmd*100,2) if cm else None
# equity TR undefined where price is missing
for r in rows:
    if r['month']>END or not spx.get(r['month']): r['equity_tr']=None
code=open(__file__).read()
meta=[{"label":"build_us.py","value":code,"type":"code","lang":"python"},
      {"label":"Inputs","value":"FRED: UNRATE, FEDFUNDS, GS10, GS2, CPIAUCSL, PCEPILFE, PALLFNFINDEXM, GDPC1, GDPPOT, NROU. ISM Manufacturing PMI (ISM releases; YCharts from Sep 2022). multpl.com: S&P 500 trailing EPS, P/E, dividend yield, monthly average price. datahub/Shiller S&P 500 monthly average price."},
      {"label":"Built","value":__import__("datetime").date.today().isoformat()}]
json.dump({"rows":rows,"meta":meta},open('compass_monthly.json','w'),separators=(',',':'))
print(len(rows)); 
for r in rows[12:15]+rows[-4:]: print(r)
