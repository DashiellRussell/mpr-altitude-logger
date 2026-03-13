/**
 * Post-flight text report generator.
 *
 * Produces a human-readable .txt with summary stats, ASCII art flight profile,
 * state timeline, and full frame log table.
 */

import type { FlightFrame, FlightStats, SimSummary } from './types.js';
import { STATE_NAMES } from './constants.js';

const W = 72; // report width
const CHART_W = 60;
const CHART_H = 20;

function pad(s: string, n: number, ch = ' '): string {
  return s.length >= n ? s : s + ch.repeat(n - s.length);
}
function rpad(s: string, n: number): string {
  return s.length >= n ? s : ' '.repeat(n - s.length) + s;
}
function center(s: string, n: number): string {
  const left = Math.floor((n - s.length) / 2);
  return ' '.repeat(Math.max(0, left)) + s + ' '.repeat(Math.max(0, n - s.length - left));
}
function line(ch = '─'): string { return ch.repeat(W); }
function dblLine(): string { return '═'.repeat(W); }
function boxLine(text: string): string {
  return '║ ' + pad(text, W - 4) + ' ║';
}

function rocketAscii(): string[] {
  return [
    `            /\\`,
    `           /  \\`,
    `          / ** \\`,
    `         /      \\`,
    `        |  UNSW  |`,
    `        | Rktry  |`,
    `        |  MPR   |`,
    `        |________|`,
    `       /|  ||||  |\\`,
    `      / |  ||||  | \\`,
    `     /  |  ||||  |  \\`,
    `    /___|________|___\\`,
    `        |\\||||/|`,
    `         \\\\||//`,
    `          \\\\//`,
    `           \\/`,
  ];
}

/** Right-align value inside a box row of fixed inner width */
function boxRow(label: string, value: string, innerW: number): string {
  const content = label + rpad(value, innerW - label.length);
  return `│ ${content} │`;
}
function boxEmpty(innerW: number): string {
  return `│${' '.repeat(innerW + 2)}│`;
}
function boxTop(innerW: number): string {
  return `┌${'─'.repeat(innerW + 2)}┐`;
}
function boxBot(innerW: number): string {
  return `└${'─'.repeat(innerW + 2)}┘`;
}

function altitudeChart(frames: FlightFrame[], stats: FlightStats): string[] {
  if (!frames.length) return ['  (no data)'];

  const t0 = frames[0].timestamp_ms;
  const times = frames.map(f => (f.timestamp_ms - t0) / 1000);
  const alts = frames.map(f => f.alt_filtered_m);

  let minAlt = Math.min(...alts);
  let maxAlt = Math.max(...alts);
  if (maxAlt - minAlt < 1) maxAlt = minAlt + 1;
  const padding = (maxAlt - minAlt) * 0.05;
  maxAlt += padding;
  minAlt = Math.min(0, minAlt - padding); // Always show ground
  const maxT = times[times.length - 1] || 1;

  // State characters for the fill
  const stateChars: Record<string, string> = {
    PAD: '·', BOOST: '█', COAST: '▓', APOGEE: '◆',
    DROGUE: '▒', MAIN: '░', LANDED: '·',
  };

  const lines: string[] = [];
  const labelW = 7;

  for (let row = CHART_H; row >= 0; row--) {
    const threshold = minAlt + (row / CHART_H) * (maxAlt - minAlt);
    let rowStr = '';

    // Y-axis label every 5 rows
    if (row % 5 === 0 || row === CHART_H) {
      rowStr += rpad(`${threshold.toFixed(0)}m`, labelW - 1) + '│';
    } else {
      rowStr += ' '.repeat(labelW - 1) + '│';
    }

    for (let col = 0; col < CHART_W; col++) {
      const t = (col / CHART_W) * maxT;
      // Find nearest frame
      let bestIdx = 0;
      let bestDist = Infinity;
      for (let i = 0; i < times.length; i++) {
        const d = Math.abs(times[i] - t);
        if (d < bestDist) { bestDist = d; bestIdx = i; }
      }
      const alt = alts[bestIdx];
      const stateName = STATE_NAMES[frames[bestIdx].state] ?? 'PAD';

      if (alt >= threshold) {
        rowStr += stateChars[stateName] ?? '█';
      } else {
        rowStr += ' ';
      }
    }
    lines.push(rowStr);
  }

  // X-axis
  lines.push(' '.repeat(labelW) + '└' + '─'.repeat(CHART_W));

  // Time labels
  const nLabels = 6;
  let tLabels = ' '.repeat(labelW) + ' ';
  for (let i = 0; i <= nLabels; i++) {
    const pos = Math.floor((i * CHART_W) / nLabels);
    const t = (pos / CHART_W) * maxT;
    const lbl = `${t.toFixed(0)}s`;
    while (tLabels.length < labelW + 1 + pos) tLabels += ' ';
    tLabels += lbl;
  }
  lines.push(tLabels);

  // Legend
  lines.push('');
  lines.push(`  █ BOOST  ▓ COAST  ◆ APOGEE  ▒ DROGUE  ░ MAIN  · PAD/LANDED`);

  return lines;
}

function stateTimeline(stats: FlightStats, duration: number): string[] {
  const lines: string[] = [];
  const barW = 50;

  if (!stats.transitions.length) {
    lines.push('  No state transitions detected (PAD-only flight)');
    return lines;
  }

  // Build segments
  interface Seg { state: string; start: number; end: number }
  const segs: Seg[] = [];
  let prev = 'PAD';
  let prevT = 0;
  for (const tr of stats.transitions) {
    segs.push({ state: prev, start: prevT, end: tr.time });
    prev = tr.to_state;
    prevT = tr.time;
  }
  segs.push({ state: prev, start: prevT, end: duration });

  const stateSymbol: Record<string, string> = {
    PAD: '·', BOOST: '█', COAST: '▓', APOGEE: '◆',
    DROGUE: '▒', MAIN: '░', LANDED: '_',
  };

  let bar = '';
  for (const seg of segs) {
    const w = Math.max(1, Math.round((seg.end - seg.start) / duration * barW));
    bar += (stateSymbol[seg.state] ?? '?').repeat(w);
  }
  lines.push(`  T=0${'─'.repeat(barW - 6)}T=${duration.toFixed(0)}s`);
  lines.push(`  ${bar.slice(0, barW)}`);
  lines.push('');

  // Transition list
  for (const tr of stats.transitions) {
    lines.push(`  T+${tr.time.toFixed(2).padStart(7)}s   ${pad(tr.from_state, 7)} → ${tr.to_state}`);
  }

  return lines;
}

function frameTable(frames: FlightFrame[], version: number): string[] {
  const lines: string[] = [];
  const t0 = frames[0]?.timestamp_ms ?? 0;

  const hdr = version >= 2
    ? '  #       T(ms)   T+s     State   P(Pa)     Temp   AltRaw  AltFilt  Vel m/s  3V3   5V    9V   Flags'
    : '  #       T(ms)   T+s     State   P(Pa)     Temp   AltRaw  AltFilt  Vel m/s  Batt  Flags';
  lines.push(hdr);
  lines.push('  ' + '─'.repeat(hdr.length - 2));

  for (let i = 0; i < frames.length; i++) {
    const f = frames[i];
    const t = ((f.timestamp_ms - t0) / 1000).toFixed(2);
    const stateName = STATE_NAMES[f.state] ?? '?';
    const flagStr = f.flags ? `0x${f.flags.toString(16).padStart(2, '0')}` : '  ·';

    let row = `  ${String(i).padStart(5)} `
      + `${String(f.timestamp_ms).padStart(8)} `
      + `${rpad(t, 6)}  `
      + `${pad(stateName, 7)} `
      + `${f.pressure_pa.toFixed(0).padStart(8)}  `
      + `${f.temperature_c.toFixed(1).padStart(5)}  `
      + `${f.alt_raw_m.toFixed(2).padStart(7)}  `
      + `${f.alt_filtered_m.toFixed(2).padStart(7)}  `
      + `${((f.vel_filtered_ms >= 0 ? '+' : '') + f.vel_filtered_ms.toFixed(2)).padStart(7)}  `;

    if (version >= 2) {
      row += `${String(f.v_3v3_mv ?? 0).padStart(4)}  `
        + `${String(f.v_5v_mv ?? 0).padStart(4)}  `
        + `${String(f.v_9v_mv ?? 0).padStart(4)}  `;
    } else {
      row += `${String(f.v_batt_mv ?? 0).padStart(4)}  `;
    }
    row += flagStr;
    lines.push(row);
  }

  return lines;
}

/**
 * Generate a full post-flight text report.
 */
export function generateFlightReport(
  frames: FlightFrame[],
  stats: FlightStats,
  version: number,
  filename: string,
  sim?: SimSummary | null,
): string {
  const out: string[] = [];
  const t0 = frames[0]?.timestamp_ms ?? 0;
  const tEnd = frames[frames.length - 1]?.timestamp_ms ?? 0;

  // ── Header ──
  out.push(dblLine());
  out.push(center('UNSW ROCKETRY — MPR ALTITUDE LOGGER', W));
  out.push(center('POST-FLIGHT REPORT', W));
  out.push(dblLine());
  out.push('');
  out.push(`  Source:       ${filename}`);
  out.push(`  Log version:  ${version}`);
  out.push(`  Frames:       ${stats.nFrames.toLocaleString()}`);
  out.push(`  Sample rate:  ${stats.sampleRate.toFixed(1)} Hz`);
  out.push(`  Duration:     ${stats.duration.toFixed(1)} s`);
  out.push(`  Errors:       ${stats.hadError ? 'YES — check flags column' : 'None'}`);
  out.push('');

  // ── Rocket art + key stats ──
  out.push(line());
  out.push(center('FLIGHT PROFILE', W));
  out.push(line());
  out.push('');

  const rocket = rocketAscii();
  const bw = 30; // inner width of stats box
  const statsBlock = [
    boxTop(bw),
    boxRow('  APOGEE', '', bw),
    boxRow('  ', stats.maxAlt.toFixed(1) + ' m AGL', bw),
    boxRow('  ', '@ T+' + stats.maxAltTime.toFixed(2) + 's', bw),
    boxEmpty(bw),
    boxRow('  MAX VELOCITY', '', bw),
    boxRow('  ', stats.maxVel.toFixed(1) + ' m/s', bw),
    boxRow('  ', '@ T+' + stats.maxVelTime.toFixed(2) + 's', bw),
    boxEmpty(bw),
    boxRow('  MAX ACCEL (est.)', '', bw),
    boxRow('  ', '~' + stats.maxAccel.toFixed(1) + ' m/s²', bw),
    boxEmpty(bw),
    boxRow('  LANDING VEL', '', bw),
    boxRow('  ', stats.landingVel.toFixed(1) + ' m/s', bw),
    boxEmpty(bw),
    boxBot(bw),
  ];

  // Side-by-side: rocket on left, stats on right
  const maxLines = Math.max(rocket.length, statsBlock.length);
  for (let i = 0; i < maxLines; i++) {
    const left = i < rocket.length ? pad(rocket[i], 34) : ' '.repeat(34);
    const right = i < statsBlock.length ? statsBlock[i] : '';
    out.push(`  ${left}  ${right}`);
  }
  out.push('');

  // ── Altitude chart ──
  out.push(line());
  out.push(center('ALTITUDE vs TIME', W));
  out.push(line());
  out.push('');
  for (const l of altitudeChart(frames, stats)) {
    out.push(l);
  }
  out.push('');

  // ── State timeline ──
  out.push(line());
  out.push(center('STATE TIMELINE', W));
  out.push(line());
  out.push('');
  for (const l of stateTimeline(stats, stats.duration)) {
    out.push(l);
  }
  out.push('');

  // ── Sim comparison ──
  if (sim) {
    out.push(line());
    out.push(center('SIMULATION COMPARISON', W));
    out.push(line());
    out.push('');
    const altDiff = stats.maxAlt - sim.maxAlt;
    const altPct = sim.maxAlt > 0 ? (altDiff / sim.maxAlt * 100) : 0;
    const velDiff = stats.maxVel - sim.maxVel;
    const velPct = sim.maxVel > 0 ? (velDiff / sim.maxVel * 100) : 0;
    out.push(`  ${''.padEnd(20)}${'Actual'.padStart(12)}${'Simulated'.padStart(12)}${'Delta'.padStart(12)}`);
    out.push(`  ${'─'.repeat(56)}`);
    out.push(`  ${'Apogee (m)'.padEnd(20)}${stats.maxAlt.toFixed(1).padStart(12)}${sim.maxAlt.toFixed(1).padStart(12)}${(altDiff >= 0 ? '+' : '') + altDiff.toFixed(1).padStart(11)} (${altPct >= 0 ? '+' : ''}${altPct.toFixed(1)}%)`);
    out.push(`  ${'Max Velocity (m/s)'.padEnd(20)}${stats.maxVel.toFixed(1).padStart(12)}${sim.maxVel.toFixed(1).padStart(12)}${(velDiff >= 0 ? '+' : '') + velDiff.toFixed(1).padStart(11)} (${velPct >= 0 ? '+' : ''}${velPct.toFixed(1)}%)`);
    out.push('');
  }

  // ── Power rails ──
  if (stats.version >= 2 && stats.v3v3Range && stats.v5vRange && stats.v9vRange) {
    out.push(line());
    out.push(center('POWER RAILS', W));
    out.push(line());
    out.push('');
    const fmt = (range: [number, number], nom: number) => {
      const lo = (range[0] / 1000).toFixed(2);
      const hi = (range[1] / 1000).toFixed(2);
      const ok = range[0] / 1000 > nom * 0.85 ? 'OK' : 'LOW';
      return `${lo}V — ${hi}V  [${ok}]`;
    };
    out.push(`  3.3V rail:  ${fmt(stats.v3v3Range, 3.3)}`);
    out.push(`  5.0V rail:  ${fmt(stats.v5vRange, 5.0)}`);
    out.push(`  9.0V rail:  ${fmt(stats.v9vRange, 9.0)}`);
    out.push('');
  }

  // ── Full frame log ──
  out.push(dblLine());
  out.push(center('DETAILED FRAME LOG', W));
  out.push(dblLine());
  out.push('');
  out.push(`  ${stats.nFrames.toLocaleString()} frames, ${stats.sampleRate.toFixed(1)} Hz, ${stats.duration.toFixed(1)}s total`);
  out.push('');
  for (const l of frameTable(frames, version)) {
    out.push(l);
  }
  out.push('');
  out.push(line());
  out.push(center('END OF REPORT', W));
  out.push(line());
  out.push('');
  out.push(`Generated by MPR Ground Station — UNSW Rocketry`);
  out.push('');

  return out.join('\n');
}
