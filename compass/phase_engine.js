// Cycle phase engine. Input: monthly rows (all countries). Output: one row per country-month.
// Phase I = Recovery, II = Expansion, III = Late cycle, IV = Contraction.
function phaseEngine(monthly) {
  const n = (v) => (v === null || v === undefined || v === '' || Number.isNaN(+v) ? null : +v);
  const clamp = (v) => Math.max(-3, Math.min(3, v));
  const mean = (a) => { const b = a.filter((v) => v !== null); return b.length ? b.reduce((s, v) => s + v, 0) / b.length : null; };
  const out = [];
  const byC = new Map(); for (const r of monthly) { if (!byC.has(r.country)) byC.set(r.country, []); byC.get(r.country).push(r); }
  for (const [country, rowsRaw] of byC) {
    const rows = rowsRaw.slice().sort((a, b) => (a.month < b.month ? -1 : a.month > b.month ? 1 : 0));
    // carry quarterly / lagging series forward up to 3 months so the latest month still has a reading
    const carry = (f, max = 3) => { let last = null, age = 99; return rows.map((r) => { const v = n(r[f]); if (v !== null) { last = v; age = 0; return { v, carried: false }; } age++; return age <= max && last !== null ? { v: last, carried: true } : { v: null, carried: false }; }); };
    const F = {};
    for (const f of ['output_gap', 'gdp_yoy', 'ism', 'urate', 'ugap', 'cpi_yoy', 'core_pce_yoy', 'policy_rate', 'taylor', 'slope_bp', 'eps_ttm', 'pe_ttm', 'equity_tr', 'bond_tr', 'commodity_idx']) F[f] = carry(f);
    const v = (f, i) => (i >= 0 && F[f][i] ? F[f][i].v : null);
    const lastReal = (f, i) => { while (i >= 0 && F[f][i] && F[f][i].carried) i--; return i; };
    const ch = (f, i, k) => { const j = lastReal(f, i); const a = v(f, j), b = v(f, j - k); return a !== null && b !== null ? a - b : null; };
    const pct = (f, i, k) => { const j = lastReal(f, i); const a = v(f, j), b = v(f, j - k); return a !== null && b !== null && b !== 0 ? (a / b - 1) * 100 : null; };
    // activity level: output gap, ISM and (inverted) unemployment gap on a common scale
    const L = rows.map((r, i) => mean([v('output_gap', i) === null ? null : clamp(v('output_gap', i) / 1.5), v('ism', i) === null ? null : clamp((v('ism', i) - 50) / 4), v('ugap', i) === null ? null : clamp(-v('ugap', i))]));
    const Ls = L.map((x, i) => mean([L[i], L[i - 1] ?? null, L[i - 2] ?? null]));
    const quad = (lvl, mom) => (lvl >= 0 ? (mom >= 0 ? 'II' : 'III') : mom >= 0 ? 'I' : 'IV');
    rows.forEach((r, i) => {
      const level = Ls[i];
      const mom = Ls[i] !== null && Ls[i - 3] !== undefined && Ls[i - 3] !== null ? Ls[i] - Ls[i - 3] : null;
      const vote = {};
      // 1 Output gap: sign and 3-month change
      { const g = v('output_gap', i), d = ch('output_gap', i, 3); if (g !== null && d !== null) vote.output_gap = g < 0 ? (d > 0 ? (g < -1 ? 'I' : 'II') : 'IV') : d > 0 ? 'II' : 'III'; }
      // 2 Real GDP y/y: sign and acceleration
      { const g = v('gdp_yoy', i), d = ch('gdp_yoy', i, 3); if (g !== null && d !== null) vote.gdp_yoy = g < 0 ? (d > 0 ? 'I' : 'IV') : d > 0 ? 'II' : 'III'; }
      // 3 ISM: above/below 50 and 3-month direction
      { const x = v('ism', i), d = ch('ism', i, 3); if (x !== null && d !== null) vote.ism = x < 50 ? (d > 0 ? 'I' : 'IV') : d > 0 ? 'II' : 'III'; }
      // 4 Labour: unemployment 3-month change and gap to NAIRU
      { const du = ch('urate', i, 3), g = v('ugap', i); if (du !== null && g !== null) vote.labour = du > 0.25 ? 'IV' : du < -0.1 ? (g > 0.5 ? 'I' : 'II') : g <= 0 ? 'III' : 'II'; }
      // 5-6 Inflation: level vs 2.5% and 6-month direction
      const IT = country === 'MX' ? 3.5 : 2.5; // inflation threshold: target + 0.5 (Banxico targets 3%)
          const inf = (f) => { const x = v(f, i), d = ch(f, i, 6); if (x === null || d === null) return; return d > 0.2 ? (x < IT ? 'II' : 'III') : d < -0.2 ? (x > IT ? 'IV' : 'I') : x >= IT ? 'III' : 'II'; };
      vote.cpi = inf('cpi_yoy'); vote.core_pce = inf('core_pce_yoy');
      // 7 Policy: 6-month change in policy rate and stance vs Taylor rule
      { const ff = v('policy_rate', i), d = ch('policy_rate', i, 6), t = v('taylor', i); if (ff !== null && d !== null) vote.policy = d < -0.25 ? 'IV' : d > 0.25 ? (t !== null && ff < t ? 'II' : 'III') : ff < 0.5 ? 'I' : t !== null && ff < t ? 'II' : 'III'; }
      // 8 Curve: 10y-2y slope vs 100bp and 6-month direction
      { const s = v('slope_bp', i), d = ch('slope_bp', i, 6); if (s !== null && d !== null) vote.curve = s > 100 ? (d > 0 ? 'I' : 'II') : d <= 0 ? 'III' : 'IV'; }
      // 9 Earnings: 6-month EPS growth and acceleration
      { const e = pct('eps_ttm', i, 6), e0 = pct('eps_ttm', i - 6, 6); if (e !== null) vote.eps = e < -2 ? 'IV' : e0 !== null && e0 < 0 ? 'I' : e0 === null || e > e0 ? 'II' : 'III'; }
      // 10 Valuation: 6-month P/E change against earnings direction
      { const p = pct('pe_ttm', i, 6), e = pct('eps_ttm', i, 6); if (p !== null && e !== null) vote.pe = p > 0 ? (e < 0 ? 'I' : 'II') : e > 0 ? 'III' : 'IV'; }
      // 11 Equities: 6-month total return
      { const x = pct('equity_tr', i, 6); if (x !== null) vote.equity = x < -5 ? 'IV' : x > 12 ? 'I' : x > 5 ? 'II' : 'III'; }
      // 12 Bonds: 6-month total return (yields falling = positive)
      { const x = pct('bond_tr', i, 6), eq = pct('equity_tr', i, 6), cm = pct('commodity_idx', i, 6); if (x !== null) vote.bonds = x > 2 ? (eq !== null && eq < 0 ? 'IV' : 'I') : x < -2 ? (cm !== null && cm > 0 ? 'III' : 'II') : 'II'; }
      // 13 Commodities: 6-month change
      { const x = pct('commodity_idx', i, 6); if (x !== null) vote.commodities = x > 10 ? 'III' : x > 0 ? 'II' : x > -10 ? 'I' : 'IV'; }
      const clock = level !== null && mom !== null ? quad(level, mom) : null;
      const tally = { I: 0, II: 0, III: 0, IV: 0 };
      Object.values(vote).forEach((p) => { if (p) tally[p] += 1; });
      if (clock) tally[clock] += 3; // the activity clock counts as three votes
      const nv = Object.values(vote).filter(Boolean).length;
      const phase = clock || nv ? ['I', 'II', 'III', 'IV'].reduce((a, b) => (tally[b] > tally[a] ? b : a), 'I') : null;
      const agree = phase ? Object.values(vote).filter((p) => p === phase).length : 0;
      const dissentT = { I: 0, II: 0, III: 0, IV: 0 }; Object.values(vote).forEach((p) => { if (p && p !== phase) dissentT[p] += 1; });
      const dissent = ['I', 'II', 'III', 'IV'].reduce((a, b) => (dissentT[b] > dissentT[a] ? b : a), 'I');
      out.push({ country, month: r.month, phase_raw: phase, dissent: dissentT[dissent] ? dissent : null, dissent_n: dissentT[dissent], level: level === null ? null : +level.toFixed(3), momentum: mom === null ? null : +mom.toFixed(3), clock, phase,
        votes_I: tally.I, votes_II: tally.II, votes_III: tally.III, votes_IV: tally.IV, signals: nv, agree,
        agreement: nv ? Math.round((agree / nv) * 100) : null,
        gap_carried: F.output_gap[i].carried, ...Object.fromEntries(Object.entries(vote).map(([k, p]) => ['v_' + k, p || null])) });
    });
    // a new phase must hold two months in a row before the compass switches to it
    const mine = out.filter((o) => o.country === country);
    let cur = null, run = 0;
    mine.forEach((o, k) => {
      const prevRaw = k > 0 ? mine[k - 1].phase_raw : null;
      if (cur === null || (o.phase_raw === prevRaw && o.phase_raw !== cur)) { if (o.phase_raw !== cur) run = 0; cur = o.phase_raw ?? cur; }
      run += 1; o.phase = cur; o.months_in_phase = run;
      const vs = Object.keys(o).filter((key) => key.startsWith('v_')).map((key) => o[key]).filter(Boolean);
      o.agree = vs.filter((p) => p === cur).length; o.agreement = vs.length ? Math.round((o.agree / vs.length) * 100) : null;
      const dt = { I: 0, II: 0, III: 0, IV: 0 }; vs.forEach((p) => { if (p !== cur) dt[p] += 1; });
      const dd = ['I', 'II', 'III', 'IV'].reduce((a, b) => (dt[b] > dt[a] ? b : a), 'I'); o.dissent = dt[dd] ? dd : null; o.dissent_n = dt[dd];
    });
  }
  return out;
}
