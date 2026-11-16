"""Build UK monthly rows for the Economic Cycle Compass and merge them into compass_monthly.json.
Inputs: raw/uk_inputs.py (FRED, ONS, OECD, AMECO, Bank of England, S&P Global PMI, iShares ISF via ChartRow).
Approximations:
- Output gap: AMECO annual gap gives annual potential GDP, interpolated log-linearly between mid-years and compared
  with quarterly real GDP (FRED NGDPRSAXDCGBQ), so quarterly gaps average to the AMECO figure. 2026Q3 from ONS first estimate.
- U-gap: unemployment minus AMECO NAWRU (interpolated).
- Inflation: OECD UK CPI series (CPIH basis), all items and excl. food and energy.
- Policy: Bank Rate at month end. Slope: 10Y gilt minus 3M interbank (Bank Rate stands in from Feb 2026); y2 holds the short rate.
- Equity TR: FTSE 100 monthly total return from iShares ISF (dividends reinvested). P/E: worldperatio MSCI country ETF P/E proxy; EPS = index / P/E (raw/pe_inputs.py).
- Taylor rule: 2 + core + 0.5(core-2) + 0.5 gap."""
import json, math, sys, os
sys.path.insert(0,'raw'); import uk_inputs as E; import pe_inputs as P
PE,PEF=P.clean(P.PE_UK)
END=os.environ.get('COMPASS_END','2026-10'); YE=int(END[:4])+1
M=[f'{y}-{m:02d}' for y in range(2019,YE) for m in range(1,13) if f'{y}-{m:02d}'<=END]
def prevm(m,n):
    y,mo=map(int,m.split('-')); t=y*12+mo-1-n; return f"{t//12}-{t%12+1:02d}"
def qkey(m):
    y,mo=m.split('-'); return f"{y}-{(int(mo)-1)//3*3+1:02d}"
def prevq(k,n): return prevm(k,3*n)
# potential GDP in FRED units: per-year ratio FRED annual sum / AMECO GDP (2025 ratio for 2026-27)
ann={y:sum(E.GDPQ[f'{y}-{m:02d}'] for m in (1,4,7,10)) for y in range(2018,2026)}
ratio={y:ann[y]/E.AMECO_OVGD[y] for y in ann}
for y in E.AMECO_OVGD: ratio.setdefault(y,ratio[max(ann)])
POT={y:E.AMECO_OVGD[y]*ratio[y]/(1+E.AMECO_GAP[y]/100) for y in E.AMECO_OVGD}  # annual (sum of quarters)
def interp(d,t):  # d keyed by year, value placed at mid-year (y+0.5); log-linear
    ys=sorted(d); t=min(max(t,ys[0]+0.5),ys[-1]+0.5)
    for a,b in zip(ys,ys[1:]):
        if a+0.5<=t<=b+0.5:
            w=t-(a+0.5); return math.exp(math.log(d[a])*(1-w)+math.log(d[b])*w)
def lin(d,t):
    ys=sorted(d); t=min(max(t,ys[0]+0.5),ys[-1]+0.5)
    for a,b in zip(ys,ys[1:]):
        if a+0.5<=t<=b+0.5:
            w=t-(a+0.5); return d[a]*(1-w)+d[b]*w
US={r['month']:r for r in json.load(open('compass_monthly.json'))['rows'] if r['country']=='US'}
rows=[];bond=100.0;eq=100.0
for i,m in enumerate(M):
    y,mo=map(int,m.split('-')); qk=qkey(m)
    g=E.GDPQ.get(qk); g4=E.GDPQ.get(prevq(qk,4)); g1=E.GDPQ.get(prevq(qk,1))
    qmid=y+((int(qk[5:])-1)/3+0.5)/4
    gap=round((g*4/interp(POT,qmid)-1)*100,2) if g else None
    nawru=lin(E.AMECO_NAWRU,y+(mo-0.5)/12)
    u=E.URATE.get(m); y10=E.Y10.get(m); e3=E.I3M.get(m); core=E.CORE.get(m)
    taylor=round(2+core+0.5*(core-2)+0.5*gap,2) if core is not None and gap is not None else None
    if i>0 and y10 is not None and E.Y10.get(M[i-1]) is not None:
        c=E.Y10[M[i-1]]/100; yy=y10/100; n=20-1/6
        price=sum((c/2)/(1+yy/2)**(k-1/6) for k in range(1,21))+1/(1+yy/2)**n
        bond*=price+c/12; bt=bond
    else: bt=None if i>0 else bond
    if i>0 and E.EQ_TR.get(m) is not None: eq*=1+E.EQ_TR[m]/100
    rows.append(dict(country='UK',month=m,output_gap=gap,
        gdp_yoy=round((g/g4-1)*100,2) if g and g4 else None,
        gdp_saar=round(((g/g1)**4-1)*100,2) if g and g1 else None,
        ism=E.PMI.get(m),urate=u,ugap=round(u-nawru,2) if u is not None else None,
        cpi_yoy=E.CPI.get(m),core_pce_yoy=core,policy_rate=E.BR.get(m),taylor=taylor,
        y10=y10,y2=e3,slope_bp=round((y10-e3)*100) if y10 is not None and e3 is not None else None,
        **P.fields(PE,PEF,m,P.FTSE.get(m)),_bond=bt,_eq=eq,
        commodity_idx=US.get(m,{}).get('commodity_idx')))
b0=[r for r in rows if r['month']=='2020-01'][0]; B0=b0['_bond']; Q0=b0['_eq']
for r in rows:
    b=r.pop('_bond'); q=r.pop('_eq')
    r['bond_tr']=round(b/B0*100,2) if b is not None else None
    r['equity_tr']=round(q/Q0*100,2)
D=json.load(open('compass_monthly.json'))
D['rows']=[r for r in D['rows'] if r['country']!='UK']+rows
code=open(__file__).read(); inputs=open('raw/uk_inputs.py').read()
D['meta']=[x for x in D['meta'] if not x['label'].startswith(('build_uk','uk_inputs','UK'))]
D['meta']+=[{"label":"build_uk.py","value":code,"type":"code","lang":"python"},
 {"label":"uk_inputs.py","value":inputs,"type":"code","lang":"python"},
 {"label":"UK inputs","value":"FRED: NGDPRSAXDCGBQ, LRHUTTTTGBM156S, IRLTLT01GBM156N, IR3TIB01GBM156N. ONS Q3 2026 GDP first estimate (+0.7% q/q). OECD DF_PRICES_ALL UK CPI (all items, excl. food and energy). AMECO: AVGDGP, ZNAWRU, OVGD. Bank of England Bank Rate decisions. S&P Global UK Manufacturing PMI (MQL5 calendar). FTSE 100 total return via iShares ISF (ChartRow)."}]
json.dump(D,open('compass_monthly.json','w'),separators=(',',':'))
print(len(rows))
for r in [rows[0]]+rows[12:16]+rows[-3:]: print(r)
for y in range(2019,2027):
    g=[r['output_gap'] for r in rows if r['month'].startswith(str(y)) and r['output_gap'] is not None]
    print(y, round(sum(g)/len(g),2) if g else None, E.AMECO_GAP[y])
