// Prints the latest compass phase per country: node run_engine.mjs <compass_monthly.json> [<previous compass_monthly.json>]
import fs from 'fs';
const here = new URL('.', import.meta.url).pathname;
const engine = new Function(fs.readFileSync(here + 'phase_engine.js', 'utf8') + ';return phaseEngine;')();
const latest = (file) => {
  const out = engine(JSON.parse(fs.readFileSync(file, 'utf8')).rows);
  const by = {};
  for (const r of out) if (!by[r.country] || r.month > by[r.country].month) by[r.country] = r;
  return by;
};
const now = latest(process.argv[2]);
const prev = process.argv[3] && fs.existsSync(process.argv[3]) ? latest(process.argv[3]) : {};
for (const c of ['US', 'ES', 'UK', 'MX', 'EZ']) {
  const r = now[c]; if (!r) { console.log(c, 'no rows'); continue; }
  const p = prev[c];
  const was = p ? ` | before: ${p.month} Phase ${p.phase}${p.phase !== r.phase ? '  ** PHASE CHANGE **' : ''}` : '';
  console.log(`${c} ${r.month} Phase ${r.phase} (${r.months_in_phase} months) clock ${r.clock} raw ${r.phase_raw} agree ${r.agree}/${r.signals} dissent ${r.dissent || '-'}${was}`);
}
