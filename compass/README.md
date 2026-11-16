# Economic Cycle Compass data

Feeds the dashboard at https://claude.ai/artifact/QZMmGTpZ3zdeEPfkDN1wqo (US, Spain, UK, Mexico, euro area; 17 indicators a month since 2019).

- `raw/` frozen inputs collected by hand up to Oct 2026 (sources noted in each file).
- `fetch_compass.py` runs in GitHub Actions (`.github/workflows/compass.yml`, 3rd and 18th of each month) and writes `feed.json`: FRED, Eurostat, ECB, ONS, Bank of England, AMECO, Yahoo month-end closes, multpl and worldperatio P/E.
- `compass_tools.py prep|build` applies feed + manual entries over `raw/`, runs the five builders (`build_*.py`) and checks the result. Manual entries (PMIs, Spain/Mexico/UK CPI, Banxico rate, GDP flashes, CDS) live in the dashboard's database (`inputs/manual`) and are filled by the Claude scheduled task.
- `snapshot/` the published data as of 16 Nov 2026, used to flag large revisions to older months.
- `run_engine.mjs` prints each country's latest phase using the same engine as the page (`phase_engine.js`).

Local test: `python3 compass_tools.py build --manual manual.json --out out`.
