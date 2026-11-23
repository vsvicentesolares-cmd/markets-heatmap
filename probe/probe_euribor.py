import json, re, requests, datetime as dt
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36", "Accept-Language": "en-GB,en;q=0.9"}
U = {
 "euribor_rates_eu": "https://www.euribor-rates.eu/en/current-euribor-rates/4/euribor-rate-12-months/",
 "euribor_rates_eu_es": "https://www.euribor-rates.eu/es/tipos-euribor-actuales/4/euribor-valor-12-meses/",
 "emmi_history": "https://www.emmi-benchmarks.eu/benchmarks/euribor/rate-history/",
 "global_rates": "https://www.global-rates.com/en/interest-rates/euribor/euribor-interest-12-months.aspx",
 "ecb_daily": "https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.RT.MM.EURIBOR1YD_.HSTA?format=csvdata&lastNObservations=5",
 "ecb_monthly": "https://data-api.ecb.europa.eu/service/data/FM/M.U2.EUR.RT.MM.EURIBOR1YD_.HSTA?format=csvdata&lastNObservations=3",
 "bde_ti_1_7": "https://www.bde.es/webbe/es/estadisticas/compartido/datos/csv/ti_1_7.csv",
 "euribor_com_es": "https://www.euribor.com.es/",
 "idealista": "https://www.idealista.com/news/euribor/",
}
out = {"at": dt.datetime.utcnow().isoformat()}
for k, u in U.items():
    try:
        r = requests.get(u, headers=UA, timeout=40)
        t = r.text
        nums = re.findall(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})[^0-9]{1,200}?(-?\d\.\d{3})\s*%", t)[:12]
        out[k] = {"status": r.status_code, "len": len(t), "ctype": r.headers.get("content-type"), "pairs": nums, "head": re.sub(r"\s+", " ", t[:400]),
                  "snip": [re.sub(r"\s+", " ", t[max(0, m.start()-300):m.start()+300]) for m in list(re.finditer(r"12 ?(months|meses)|EURIBOR1Y|12M", t, re.I))[:2]]}
    except Exception as e:
        out[k] = {"err": repr(e)[:300]}
json.dump(out, open("probe/result.json", "w"), indent=1, ensure_ascii=False)
