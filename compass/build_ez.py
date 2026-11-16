"""Build euro area monthly rows for the Economic Cycle Compass and merge them into compass_monthly.json.
Inputs: raw/ez_inputs.py (Eurostat, AMECO, ECB curve via Eurostat, HCOB/S&P PMI, Euro Stoxx 50, worldperatio), raw/es_inputs.py (ECB deposit rate).
Approximations:
- Output gap: AMECO EA20 annual gap gives annual potential GDP (Eurostat annual sums / (1 + gap)) for 2018-2025;
  potential is extended to 2026-27 at its 2025 growth rate, interpolated log-linearly between mid-years and compared with quarterly GDP.
- U-gap: EA21 unemployment minus AMECO EA20 NAWRU (annual, interpolated).
- Curve: euro area all-issuer government spot curve (ECB, via Eurostat irt_euryld_m), 10Y minus 2Y.
- Equity TR: Euro Stoxx 50 month-end price + 1/12 of an assumed 3.2% dividend yield.
- P/E: harmonic weighted average of worldperatio MSCI country ETF P/Es (FR, DE, NL, ES, IT, approximate MSCI EMU weights); EPS = Euro Stoxx 50 / P/E.
- Taylor rule: 2 + core + 0.5(core-2) + 0.5 gap. Policy rate: ECB deposit facility rate (monthly average)."""
import json, math, sys, os
sys.path.insert(0,'raw'); import ez_inputs as Z; import es_inputs as E; import pe_inputs as P
END=os.environ.get('COMPASS_END','2026-10'); YE=int(END[:4])+1
M=[f'{y}-{m:02d}' for y in range(2019,YE) for m in range(1,13) if f'{y}-{m:02d}'<=END]
def prevm(m,n):
    y,mo=map(int,m.split('-')); t=y*12+mo-1-n; return f"{t//12}-{t%12+1:02d}"
def qkey(m):
    y,mo=m.split('-'); return f"{y}-{(int(mo)-1)//3*3+1:02d}"
def prevq(k,n): return prevm(k,3*n)
ann={y:sum(Z.GDPQ[f'{y}-{m:02d}'] for m in (1,4,7,10)) for y in range(2018,2026)}
POT={y:ann[y]/(1+Z.AMECO_GAP[y]/100) for y in ann}
gpot=POT[2025]/POT[2024]; POT[2026]=POT[2025]*gpot; POT[2027]=POT[2026]*gpot
def interp(d,t):
    ys=sorted(d); t=min(max(t,ys[0]+0.5),ys[-1]+0.5)
    for a,b in zip(ys,ys[1:]):
        if a+0.5<=t<=b+0.5:
            w=t-(a+0.5); return math.exp(math.log(d[a])*(1-w)+math.log(d[b])*w)
def lin(d,t):
    ys=sorted(d); t=min(max(t,ys[0]+0.5),ys[-1]+0.5)
    for a,b in zip(ys,ys[1:]):
        if a+0.5<=t<=b+0.5:
            w=t-(a+0.5); return d[a]*(1-w)+d[b]*w
# composite P/E (harmonic, fixed weights)
SER={'FR':Z.PE_FR,'DE':Z.PE_DE,'NL':Z.PE_NL,'ES':P.PE_ES,'IT':Z.PE_IT}
PE={}
for m in Z.PE_DE:
    if all(m in s for s in SER.values()): PE[m]=1/sum(Z.PE_W[c]/SER[c][m] for c in SER)
PE,PEF=P.clean(PE)
US={r['month']:r for r in json.load(open('compass_monthly.json'))['rows'] if r['country']=='US'}
rows=[];bond=100.0;eq=100.0
for i,m in enumerate(M):
    y,mo=map(int,m.split('-')); qk=qkey(m)
    g=Z.GDPQ.get(qk); g4=Z.GDPQ.get(prevq(qk,4)); g1=Z.GDPQ.get(prevq(qk,1))
    qmid=y+((int(qk[5:])-1)/3+0.5)/4
    gap=round((g*4/interp(POT,qmid)-1)*100,2) if g else None
    nawru=lin(Z.AMECO_NAWRU,y+(mo-0.5)/12)
    u=Z.URATE.get(m); y10=Z.Y10.get(m); y2=Z.Y2.get(m); core=Z.CORE.get(m)
    taylor=round(2+core+0.5*(core-2)+0.5*gap,2) if core is not None and gap is not None else None
    if i>0 and y10 is not None and Z.Y10.get(M[i-1]) is not None:
        c=Z.Y10[M[i-1]]/100; yy=y10/100; n=20-1/6
        price=sum((c/2)/(1+yy/2)**(k-1/6) for k in range(1,21))+1/(1+yy/2)**n
        bond*=price+c/12; bt=bond
    else: bt=None if i>0 else bond
    pm=prevm(m,1)
    if Z.SX5E.get(m) and Z.SX5E.get(pm): eq*=Z.SX5E[m]/Z.SX5E[pm]+Z.DIVY/1200
    rows.append(dict(country='EZ',month=m,output_gap=gap,
        gdp_yoy=round((g/g4-1)*100,2) if g and g4 else None,
        gdp_saar=round(((g/g1)**4-1)*100,2) if g and g1 else None,
        ism=Z.PMI.get(m),urate=u,ugap=round(u-nawru,2) if u is not None else None,
        cpi_yoy=Z.CPI.get(m),core_pce_yoy=core,policy_rate=E.DEPO.get(m),taylor=taylor,
        y10=round(y10,3) if y10 is not None else None,y2=round(y2,3) if y2 is not None else None,slope_bp=round((y10-y2)*100) if y10 is not None and y2 is not None else None,
        **P.fields(PE,PEF,m,Z.SX5E.get(m)),_bond=bt,_eq=eq,
        commodity_idx=US.get(m,{}).get('commodity_idx')))
b0=[r for r in rows if r['month']=='2020-01'][0]; B0=b0['_bond']; Q0=b0['_eq']
for r in rows:
    b=r.pop('_bond'); q=r.pop('_eq')
    r['bond_tr']=round(b/B0*100,2) if b is not None else None
    r['equity_tr']=round(q/Q0*100,2)
D=json.load(open('compass_monthly.json'))
D['rows']=[r for r in D['rows'] if r['country']!='EZ']+rows
D['meta']=[x for x in D['meta'] if not x['label'].startswith(('build_ez','ez_inputs','Euro area'))]
D['meta']+=[{"label":"build_ez.py","value":open(__file__).read(),"type":"code","lang":"python"},
 {"label":"ez_inputs.py","value":open('raw/ez_inputs.py').read(),"type":"code","lang":"python"},
 {"label":"Euro area inputs","value":"Eurostat: namq_10_gdp (EA20), une_rt_m (EA21), prc_hicp_manr and prc_hicp_minr (EA), irt_euryld_m (ECB all-issuer curve). AMECO: AVGDGP, ZNAWRU (EA20). FRED ECBDFR. HCOB/S&P Global Eurozone Manufacturing PMI (MetaTrader calendar). Euro Stoxx 50 month-end (Investing.com). worldperatio.com country ETF P/Es."}]
json.dump(D,open('compass_monthly.json','w'),separators=(',',':'))
print(len(rows), 'gpot', round((gpot-1)*100,2))
for r in [rows[0]]+rows[14:17]+rows[-3:]: print({k:r[k] for k in ('month','output_gap','gdp_yoy','ism','ugap','cpi_yoy','core_pce_yoy','policy_rate','taylor','slope_bp','eps_ttm','pe_ttm','bond_tr','equity_tr')})
