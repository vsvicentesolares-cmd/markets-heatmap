"""Build Spain monthly rows for the Economic Cycle Compass and merge them into compass_monthly.json.
Inputs: raw/es_inputs.py (FRED, Eurostat, INE, AMECO, HCOB/S&P PMI, Yahoo ^IBEX, BME).
Approximations:
- Output gap: AMECO annual gap gives annual potential GDP; potential is interpolated log-linearly
  between mid-years and compared with quarterly real GDP (FRED, chain-linked), so quarterly gaps
  average to the AMECO annual figure.
- U-gap: unemployment minus AMECO NAWRU (annual, interpolated between mid-years).
- Slope: 10Y bono minus 3M Euribor (no free monthly 2Y bono history). y2 field holds 3M Euribor.
- Equity TR: IBEX 35 month-end price + 1/12 of BME annual dividend yield. P/E: worldperatio MSCI country ETF P/E proxy; EPS = index / P/E (raw/pe_inputs.py).
- Taylor rule: 2 + core + 0.5(core-2) + 0.5 gap (Spain-specific counterfactual; policy is the ECB's).
- Commodities: same IMF all-commodities index as the US rows."""
import json, math, sys, os
sys.path.insert(0,'raw'); import es_inputs as E; import pe_inputs as P
PE,PEF=P.clean(P.PE_ES)
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
    u=E.URATE.get(m); y10=E.Y10.get(m); e3=E.EUR3M.get(m); core=E.CORE.get(m)
    taylor=round(2+core+0.5*(core-2)+0.5*gap,2) if core is not None and gap is not None else None
    if i>0 and y10 is not None and E.Y10.get(M[i-1]) is not None:
        c=E.Y10[M[i-1]]/100; yy=y10/100; n=20-1/6
        price=sum((c/2)/(1+yy/2)**(k-1/6) for k in range(1,21))+1/(1+yy/2)**n
        bond*=price+c/12; bt=bond
    else: bt=None if i>0 else bond
    pm=prevm(m,1)
    if E.IBEX.get(m) and E.IBEX.get(pm):
        eq*=E.IBEX[m]/E.IBEX[pm]+E.DIVY.get(max(2019,int(pm[:4])),E.DIVY[max(E.DIVY)])/1200
    rows.append(dict(country='ES',month=m,output_gap=gap,
        gdp_yoy=round((g/g4-1)*100,2) if g and g4 else None,
        gdp_saar=round(((g/g1)**4-1)*100,2) if g and g1 else None,
        ism=E.PMI.get(m),urate=u,ugap=round(u-nawru,2) if u is not None else None,
        cpi_yoy=round(E.CPI[m],2) if E.CPI.get(m) is not None else None,core_pce_yoy=core,policy_rate=E.DEPO.get(m),taylor=taylor,
        y10=y10,y2=e3,slope_bp=round((y10-e3)*100) if y10 is not None and e3 is not None else None,
        **P.fields(PE,PEF,m,E.IBEX.get(m)),_bond=bt,_eq=eq,
        commodity_idx=US.get(m,{}).get('commodity_idx')))
b0=[r for r in rows if r['month']=='2020-01'][0]; B0=b0['_bond']; Q0=b0['_eq']
for r in rows:
    b=r.pop('_bond'); q=r.pop('_eq')
    r['bond_tr']=round(b/B0*100,2) if b is not None else None
    r['equity_tr']=round(q/Q0*100,2)
D=json.load(open('compass_monthly.json'))
D['rows']=[r for r in D['rows'] if r['country']!='ES']+rows
code=open(__file__).read(); inputs=open('raw/es_inputs.py').read()
D['meta']=[x for x in D['meta'] if not x['label'].startswith(('build_es','es_inputs','Spain'))]
D['meta']+=[{"label":"build_es.py","value":code,"type":"code","lang":"python"},
 {"label":"es_inputs.py","value":inputs,"type":"code","lang":"python"},
 {"label":"Spain inputs","value":"FRED: LRHUTTTTESM156S, IRLTLT01ESM156N, ECBDFR, IR3TIB01EZM156N, CLVMNACSCAB1GQES. euribor-rates.eu (3M Euribor Feb-Oct 2026). Eurostat HICP excl. energy and food (prc_hicp_manr, prc_hicp_minr). INE IPC via RateInflation. AMECO: AVGDGP, ZNAWRU, OVGD. HCOB/S&P Global Spain Manufacturing PMI (eulerpool, S&P releases, Trading Economics). Yahoo ^IBEX month-end. BME IBEX 35 Impacto Div."}]
json.dump(D,open('compass_monthly.json','w'),separators=(',',':'))
print(len(rows))
for r in [rows[0]]+rows[12:16]+rows[-3:]: print(r)
for y in range(2019,2027):
    g=[r['output_gap'] for r in rows if r['month'].startswith(str(y)) and r['output_gap'] is not None]
    print(y, round(sum(g)/len(g),2) if g else None, E.AMECO_GAP[y])
