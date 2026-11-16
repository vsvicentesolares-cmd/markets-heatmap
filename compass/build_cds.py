"""Sovereign 5Y CDS (bp), monthly close as shown on Investing.com historical data pages (monthly view), read 14 Nov 2026.
Instruments: US CDS 5Y USD (USGV5YUSAB), Spain CDS 5Y USD (ESGV5YUSAC), UK CDS 5Y (page labelled USD), Mexico CDS 5Y USD (MXGV5YUSAC).
Nov 2026 is month to date (13 Nov). Indicative quotes; months missing on the source are left out."""
import json, datetime, os, sys
sys.path.insert(0,'raw'); import ez_inputs as Z, es_inputs as E
CUR=datetime.date.today().strftime('%Y-%m')
# later month-end closes added by compass_tools.py (manual overlay "cds.<CC>"), {country: {month: bp}}
OV=json.load(open('cds_overlay.json')) if os.path.exists('cds_overlay.json') else {}
MON={m:i+1 for i,m in enumerate('Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split())}
rows=[]
for c,f in [('US','cds_us.txt'),('ES','cds_es.txt'),('UK','cds_uk.txt'),('MX','cds_mx.txt')]:
    for line in open('raw/'+f):
        p=line.split()
        if not p: continue
        if p[0][:4].isdigit(): m,v=p[0],float(p[1])
        else: m=f"{p[2]}-{MON[p[0]]:02d}"; v=float(p[3])
        if m>='2019-01' and m not in OV.get(c,{}): rows.append({'country':c,'month':m,'cds_5y':v,'mtd':m==CUR})
    for m,v in OV.get(c,{}).items():
        if m>='2019-01': rows.append({'country':c,'month':m,'cds_5y':v,'mtd':m==CUR})
# euro area: 10Y spreads over the Bund (bp), FRED monthly averages; there is no euro area sovereign CDS
for m in sorted(Z.DE10):
    de=Z.DE10[m]; row={'country':'EZ','month':m,'cds_5y':None,'mtd':False}
    for k,src in (('it_bund',Z.IT10),('fr_bund',Z.FR10),('es_bund',E.Y10)):
        row[k]=round((src[m]-de)*100,1) if src.get(m) is not None else None
    rows.append(row)
rows.sort(key=lambda r:(r['country'],r['month']))
json.dump({'rows':rows,'meta':[{'label':'build_cds.py','value':open(__file__).read(),'type':'code','lang':'python'},{'label':'Built','value':datetime.date.today().isoformat()}]},open('compass_cds.json','w'),separators=(',',':'))
for c in ['US','ES','UK','MX','EZ']:
    r=[x for x in rows if x['country']==c]; print(c,len(r),r[0]['month'],r[-1]['month'])
