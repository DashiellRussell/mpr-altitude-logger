import React from 'react';
import { Box, Text } from 'ink';
import type { FlightFrame, StateTransition, SimSummary } from '@mpr/shared';
import { STATE_COLORS, STATE_NAMES } from '@mpr/shared';

interface AltitudeChartProps {
  frames: FlightFrame[];
  sim?: SimSummary | null;
  transitions?: StateTransition[];
  width?: number;
  height?: number;
  /** Diagonal sweep position (0 → width+height). Cells visible when col + rowFromBottom < sweep. undefined = all visible. */
  revealSweep?: number;
}

type InkColor = 'white' | 'red' | 'yellow' | 'green' | 'cyan' | 'blue' | 'magenta' | 'gray';

function toInkColor(color: string): InkColor {
  const valid: InkColor[] = ['white', 'red', 'yellow', 'green', 'cyan', 'blue', 'magenta', 'gray'];
  return valid.includes(color as InkColor) ? (color as InkColor) : 'cyan';
}

/**
 * ASCII altitude profile using block characters, coloured by flight state.
 * Port of render_altitude_chart from postflight.py.
 */
export function AltitudeChart({
  frames,
  sim,
  transitions,
  width = 60,
  height = 18,
  revealSweep,
}: AltitudeChartProps) {
  if (!frames.length) {
    return <Text dimColor>(no data)</Text>;
  }

  const t0 = frames[0].timestamp_ms;
  const times = frames.map((f) => (f.timestamp_ms - t0) / 1000);
  const altitudes = frames.map((f) => f.alt_filtered_m);
  const states = frames.map((f) => STATE_NAMES[f.state] ?? 'PAD');

  let minAlt = Math.min(...altitudes);
  let maxAlt = Math.max(...altitudes);
  if (sim && sim.altitudes.length) {
    maxAlt = Math.max(maxAlt, Math.max(...sim.altitudes));
    minAlt = Math.min(minAlt, Math.min(...sim.altitudes));
  }
  // Ensure some visible range even for ground-only data
  const range = maxAlt - minAlt;
  if (range < 1.0) {
    maxAlt = minAlt + 1.0;
  }
  // Add 10% padding
  const padding = (maxAlt - minAlt) * 0.1;
  maxAlt += padding;
  minAlt -= padding;

  const maxT = Math.max(...times);
  if (maxT <= 0) {
    return <Text dimColor>(no time data)</Text>;
  }

  const labelW = 7;

  // Pre-compute the nearest frame index and sim index for each column
  const colFrameIdx: number[] = [];
  const colSimIdx: number[] = [];
  for (let col = 0; col < width; col++) {
    const t = (col / width) * maxT;

    let bestIdx = 0;
    let bestDist = Infinity;
    for (let i = 0; i < times.length; i++) {
      const d = Math.abs(times[i] - t);
      if (d < bestDist) {
        bestDist = d;
        bestIdx = i;
      }
    }
    colFrameIdx.push(bestIdx);

    if (sim && sim.times.length) {
      let sBestIdx = 0;
      let sBestDist = Infinity;
      for (let i = 0; i < sim.times.length; i++) {
        const d = Math.abs(sim.times[i] - t);
        if (d < sBestDist) {
          sBestDist = d;
          sBestIdx = i;
        }
      }
      colSimIdx.push(sBestIdx);
    }
  }

  // Build chart rows as arrays of {char, color} for state-coloured rendering
  interface Cell { char: string; color: InkColor }
  const chartRows: Cell[][] = [];

  for (let row = height; row >= 0; row--) {
    const threshold = minAlt + (row / height) * (maxAlt - minAlt);
    const rowCells: Cell[] = [];

    for (let col = 0; col < width; col++) {
      const fIdx = colFrameIdx[col];
      const alt = altitudes[fIdx];
      const stateName = states[fIdx];
      const stateColor = toInkColor(STATE_COLORS[stateName] ?? 'cyan');

      const simHit = sim && colSimIdx[col] !== undefined && sim.altitudes[colSimIdx[col]] >= threshold;

      if (alt >= threshold) {
        rowCells.push({ char: '\u2588', color: stateColor });
      } else if (simHit) {
        rowCells.push({ char: '\u2591', color: 'gray' });
      } else {
        rowCells.push({ char: ' ', color: 'white' });
      }
    }
    chartRows.push(rowCells);
  }

  // Group consecutive cells with same colour to reduce React element count
  // Diagonal sweep: cell visible when col + rowFromBottom < revealSweep
  function renderRow(cells: Cell[], rowFromBottom: number): React.ReactNode {
    const groups: Array<{ color: InkColor; text: string }> = [];
    for (let c = 0; c < cells.length; c++) {
      const visible = revealSweep === undefined || (c + rowFromBottom) < revealSweep;
      const char = visible ? cells[c].char : ' ';
      const color: InkColor = visible ? cells[c].color : 'white';
      const last = groups[groups.length - 1];
      if (last && last.color === color) {
        last.text += char;
      } else {
        groups.push({ color, text: char });
      }
    }
    return groups.map((g, i) => (
      <Text key={i} color={g.color}>{g.text}</Text>
    ));
  }

  // X-axis
  const xAxis = '\u2500'.repeat(width);

  // Time labels
  const nLabels = Math.min(6, Math.floor(width / 10));
  let timeLabels = '';
  for (let i = 0; i <= nLabels; i++) {
    const pos = Math.floor((i * width) / Math.max(nLabels, 1));
    const t = (pos / width) * maxT;
    const lbl = `${t.toFixed(0)}s`;
    while (timeLabels.length < pos) {
      timeLabels += ' ';
    }
    timeLabels += lbl;
  }

  // Event markers
  let eventLine = '';
  if (transitions && transitions.length) {
    for (const tr of transitions) {
      const pos = Math.min(Math.floor((tr.time / maxT) * width), width - 1);
      while (eventLine.length < pos) {
        eventLine += ' ';
      }
      eventLine += `v${tr.to_state}`;
    }
  }

  // Legend
  let legend = '  \u2588 Actual (coloured by state)';
  if (sim) {
    legend += '  \u2591 Simulated';
  }

  return (
    <Box flexDirection="column" marginBottom={1}>
      <Text bold>ALTITUDE PROFILE</Text>
      {chartRows.map((cells, i) => {
        const row = height - i;  // altitude row (0=bottom, height=top)
        let label: string;
        if (row % 4 === 0 || row === height) {
          label = `${(minAlt + (row / height) * (maxAlt - minAlt)).toFixed(0)}m`.padStart(labelW - 1);
        } else {
          label = ' '.repeat(labelW - 1);
        }
        return (
          <Text key={i}>
            <Text dimColor>{label}</Text>
            <Text dimColor>|</Text>
            {renderRow(cells, row)}
          </Text>
        );
      })}
      <Text>
        <Text dimColor>{' '.repeat(labelW)}</Text>
        <Text dimColor>{xAxis}</Text>
      </Text>
      <Text dimColor>{' '.repeat(labelW)}{timeLabels}</Text>
      {eventLine && <Text dimColor>{' '.repeat(labelW)}{eventLine}</Text>}
      <Text dimColor>{legend}</Text>
    </Box>
  );
}
