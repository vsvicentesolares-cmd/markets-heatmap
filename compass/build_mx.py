"""Build Mexico monthly rows for the Economic Cycle Compass and merge them into compass_monthly.json.
Inputs: raw/mx_inputs.py (FRED/OECD, INEGI, Banxico, S&P Global PMI, Yahoo ^MXX).
Approximations:
- Output gap: log real GDP minus its Hodrick-Prescott trend (lambda 1600, 2005Q1 to 2026Q3; 2026Q3 from INEGI flash).
  End-of-sample HP estimates get revised as new quarters arrive.
- U-gap: unemployment minus its HP trend (lambda 129600, monthly, 2005 to Aug 2026) as a NAIRU proxy.
- Core: OECD CPI ex food and energy to Jul 2024 (OECD series ends), INEGI subyacente from Aug 2024 (levels match at the splice).
- Policy: Banxico target rate at month end. Slope: 10Y minus 3M TIIE (no free monthly 2Y Mbono history); y2 holds 3M TIIE.
- 10Y: single missing OECD months interpolated linearly.
- Equity TR: IPC month-end price + 1/12 of an assumed 3.5% dividend yield. P/E: worldperatio MSCI country ETF P/E proxy; EPS = index / P/E (raw/pe_inputs.py).
- Taylor rule: 2.5 neutral real rate + core + 0.5(core-3) + 0.5 gap (Banxico targets 3%)."""
import json, math, sys, os
import numpy as np
sys.path.insert(0,'raw'); import mx_inputs as X; import pe_inputs as P
PE,PEF=P.clean(P.PE_MX)
END=os.environ.get('COMPASS_END','2026-10'); YE=int(END[:4])+1
M=[f'{y}-{m:02d}' for y in range(2019,YE) for m in range(1,13) if f'{y}-{m:02d}'<=END]
def prevm(m,n):
    y,mo=map(int,m.split('-')); t=y*12+mo-1-n; return f"{t//12}-{t%12+1:02d}"
def qkey(m):
    y,mo=m.split('-'); return f"{y}-{(int(mo)-1)//3*3+1:02d}"
def hp(y,lam):
    n=len(y); D=np.zeros((n-2,n))
    for i in range(n-2): D[i,i:i+3]=[1,-2,1]
    return np.linalg.solve(np.eye(n)+lam*D.T@D, np.asarray(y,float))
qk=sorted(X.GDPQ); lg=np.log([X.GDPQ[k] for k in qk]); tr=hp(lg,1600)
GAP={k:round((a-b)*100,2) for k,a,b in zip(qk,lg,tr)}
uk=sorted(X.URATE); ut=hp([X.URATE[k] for k in uk],129600); UTR=dict(zip(uk,ut))
# interpolate single missing 10Y months
Y10=dict(X.Y10)
for m in M:
    if Y10.get(m) is None and Y10.get(prevm(m,1)) is not None and Y10.get(prevm(m,-1)) is not None:
        Y10[m]=round((Y10[prevm(m,1)]+Y10[prevm(m,-1)])/2,3)
US={r['month']:r for r in json.load(open('compass_monthly.json'))['rows'] if r['country']=='US'}
rows=[];bond=100.0;eq=100.0
for i,m in enumerate(M):
    q=qkey(m); g=X.GDPQ.get(q); g4=X.GDPQ.get(prevm(q,12)); g1=X.GDPQ.get(prevm(q,3))
    gap=GAP.get(q); u=X.URATE.get(m); y10=Y10.get(m); t3=X.TIIE3M.get(m); core=X.CORE.get(m)
    taylor=round(2.5+core+0.5*(core-3)+0.5*gap,2) if core is not None and gap is not None else None
    if i>0 and y10 is not None and Y10.get(M[i-1]) is not None:
        c=Y10[M[i-1]]/100; yy=y10/100; n=20-1/6
        price=sum((c/2)/(1+yy/2)**(k-1/6) for k in range(1,21))+1/(1+yy/2)**n
        bond*=price+c/12; bt=bond
    else: bt=None if i>0 else bond
    pm=prevm(m,1)
    if X.IPC.get(m) and X.IPC.get(pm): eq*=X.IPC[m]/X.IPC[pm]+X.DIVY/1200
    rows.append(dict(country='MX',month=m,output_gap=gap,
        gdp_yoy=round((g/g4-1)*100,2) if g and g4 else None,
        gdp_saar=round(((g/g1)**4-1)*100,2) if g and g1 else None,
        ism=X.PMI.get(m),urate=round(u,2) if u is not None else None,
        ugap=round(u-UTR[m],2) if u is not None and m in UTR else None,
        cpi_yoy=round(X.CPI[m],2) if X.CPI.get(m) is not None else None,core_pce_yoy=core,policy_rate=X.POLICY.get(m),taylor=taylor,
        y10=y10,y2=t3,slope_bp=round((y10-t3)*100) if y10 is not None and t3 is not None else None,
        **P.fields(PE,PEF,m,X.IPC.get(m)),_bond=bt,_eq=eq,
        commodity_idx=US.get(m,{}).get('commodity_idx')))
b0=[r for r in rows if r['month']=='2020-01'][0]; B0=b0['_bond']; Q0=b0['_eq']
for r in rows:
    b=r.pop('_bond'); q=r.pop('_eq')
    r['bond_tr']=round(b/B0*100,2) if b is not None else None
    r['equity_tr']=round(q/Q0*100,2)
D=json.load(open('compass_monthly.json'))
D['rows']=[r for r in D['rows'] if r['country']!='MX']+rows
D['meta']=[x for x in D['meta'] if not x['label'].startswith(('build_mx','mx_inputs','Mexico'))]
D['meta']+=[{"label":"build_mx.py","value":open(__file__).read(),"type":"code","lang":"python"},
 {"label":"mx_inputs.py","value":open('raw/mx_inputs.py').read(),"type":"code","lang":"python"},
 {"label":"Mexico inputs","value":"FRED: NGDPRSAXDCMXQ, LRHUTTTTMXM156S, IRLTLT01MXM156N, IR3TIB01MXM156N. INEGI: GDP flash Q3 2026, INPC headline (via RateInflation/CEFP), subyacente from Aug 2024. OECD DF_PRICES_ALL CPI ex food and energy to Jul 2024. Banxico policy announcements. S&P Global Mexico Manufacturing PMI (MQL5 calendar, Trading Economics, S&P releases). Yahoo ^MXX month-end."}]
json.dump(D,open('compass_monthly.json','w'),separators=(',',':'))
print(len(rows))
for r in [rows[0],rows[15],rows[-1]]: print(r)
print('gap by year',{y:round(np.mean([GAP[f'{y}-{m:02d}'] for m in (1,4,7,10) if f'{y}-{m:02d}' in GAP]),2) for y in range(2019,2027)})
print('ugap last',[(m,round(X.URATE[m]-UTR[m],2)) for m in ('2019-12','2023-06','2026-08')])
