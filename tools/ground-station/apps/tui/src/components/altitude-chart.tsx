import React from 'react';
import { Box, Text } from 'ink';
import type { FlightFrame, StateTransition, SimSummary } from '@mpr/shared';
import { STATE_COLORS } from '@mpr/shared';

interface AltitudeChartProps {
  frames: FlightFrame[];
  sim?: SimSummary | null;
  transitions?: StateTransition[];
  width?: number;
  height?: number;
}

/**
 * ASCII altitude profile using block characters.
 * Port of render_altitude_chart from postflight.py.
 */
export function AltitudeChart({
  frames,
  sim,
  transitions,
  width = 60,
  height = 18,
}: AltitudeChartProps) {
  if (!frames.length) {
    return <Text dimColor>(no data)</Text>;
  }

  const t0 = frames[0].timestamp_ms;
  const times = frames.map((f) => (f.timestamp_ms - t0) / 1000);
  const altitudes = frames.map((f) => f.alt_filtered_m);

  let maxAlt = Math.max(...altitudes) * 1.1;
  if (sim && sim.altitudes.length) {
    maxAlt = Math.max(maxAlt, Math.max(...sim.altitudes) * 1.1);
  }
  maxAlt = Math.max(maxAlt, 1.0);
  const maxT = Math.max(...times);
  if (maxT <= 0) {
    return <Text dimColor>(no time data)</Text>;
  }

  const labelW = 7;
  const lines: string[] = [];

  // Build the chart rows top-down
  for (let row = height; row >= 0; row--) {
    const threshold = (row / height) * maxAlt;

    // Y-axis label
    let label: string;
    if (row % 4 === 0 || row === height) {
      label = `${threshold.toFixed(0)}m`.padStart(labelW - 1);
    } else {
      label = ' '.repeat(labelW - 1);
    }
    label += '|';

    let rowChars = '';
    for (let col = 0; col < width; col++) {
      const t = (col / width) * maxT;

      // Find nearest altitude
      let bestIdx = 0;
      let bestDist = Infinity;
      for (let i = 0; i < times.length; i++) {
        const d = Math.abs(times[i] - t);
        if (d < bestDist) {
          bestDist = d;
          bestIdx = i;
        }
      }
      const alt = altitudes[bestIdx];

      // Check sim
      let simHit = false;
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
        if (sim.altitudes[sBestIdx] >= threshold) {
          simHit = true;
        }
      }

      if (alt >= threshold) {
        rowChars += '\u2588'; // full block (actual)
      } else if (simHit) {
        rowChars += '\u2591'; // light shade (sim)
      } else {
        rowChars += ' ';
      }
    }

    lines.push(label + rowChars);
  }

  // X-axis
  lines.push(' '.repeat(labelW) + '\u2500'.repeat(width));

  // Time labels
  const nLabels = Math.min(6, Math.floor(width / 10));
  let timeLabels = ' '.repeat(labelW);
  for (let i = 0; i <= nLabels; i++) {
    const pos = Math.floor((i * width) / Math.max(nLabels, 1));
    const t = (pos / width) * maxT;
    const lbl = `${t.toFixed(0)}s`;
    while (timeLabels.length < labelW + pos) {
      timeLabels += ' ';
    }
    timeLabels += lbl;
  }
  lines.push(timeLabels);

  // Event markers
  if (transitions && transitions.length) {
    let eventLine = ' '.repeat(labelW);
    for (const tr of transitions) {
      const pos = Math.min(
        Math.floor((tr.time / maxT) * width),
        width - 1
      );
      while (eventLine.length < labelW + pos) {
        eventLine += ' ';
      }
      eventLine += `v${tr.to_state}`;
    }
    lines.push(eventLine);
  }

  // Legend
  let legend = '  \u2588 Actual';
  if (sim) {
    legend += '  \u2591 Simulated';
  }

  return (
    <Box flexDirection="column" marginBottom={1}>
      <Text bold>ALTITUDE PROFILE</Text>
      {lines.map((line, i) => (
        <Text key={i} color="blue">
          {line}
        </Text>
      ))}
      <Text dimColor>{legend}</Text>
    </Box>
  );
}
