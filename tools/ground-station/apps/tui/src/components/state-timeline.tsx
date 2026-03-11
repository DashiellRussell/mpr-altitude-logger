import React from 'react';
import { Box, Text } from 'ink';
import type { FlightStats } from '@mpr/shared';
import { STATE_COLORS, STATE_NAMES } from '@mpr/shared';

interface StateTimelineProps {
  stats: FlightStats;
  frames: Array<{ state: number; timestamp_ms: number }>;
  width?: number;
}

type InkColor = 'white' | 'red' | 'yellow' | 'green' | 'cyan' | 'blue' | 'magenta' | 'gray';

function toInkColor(color: string): InkColor {
  const valid: InkColor[] = ['white', 'red', 'yellow', 'green', 'cyan', 'blue', 'magenta', 'gray'];
  return valid.includes(color as InkColor) ? (color as InkColor) : 'white';
}

/** Colored state segments showing the flight phase timeline */
export function StateTimeline({ stats, frames, width = 60 }: StateTimelineProps) {
  if (!frames.length || stats.duration <= 0) {
    return <Text dimColor>(no data)</Text>;
  }

  const t0 = frames[0].timestamp_ms;
  const maxT = stats.duration;

  // Build state segments
  const segments: Array<{ start: number; end: number; state: string }> = [];
  let currentState = STATE_NAMES[frames[0].state] ?? '?';
  let startT = 0;

  for (const tr of stats.transitions) {
    segments.push({ start: startT, end: tr.time, state: currentState });
    currentState = tr.to_state;
    startT = tr.time;
  }
  segments.push({ start: startT, end: maxT, state: currentState });

  return (
    <Box flexDirection="column" marginBottom={1}>
      <Text bold>STATE TIMELINE</Text>
      <Box>
        <Text>  </Text>
        {segments.map((seg, i) => {
          const frac = (seg.end - seg.start) / maxT;
          const nChars = Math.max(1, Math.round(frac * width));
          const color = toInkColor(STATE_COLORS[seg.state] ?? 'white');
          return (
            <React.Fragment key={i}>
              <Text bold color={color}>
                {seg.state}
              </Text>
              <Text> </Text>
              <Text color={color}>
                {'\u2588'.repeat(nChars)}
              </Text>
              <Text> </Text>
            </React.Fragment>
          );
        })}
      </Box>
      <Box>
        <Text>  </Text>
        {segments.map((seg, i) => {
          const frac = (seg.end - seg.start) / maxT;
          const nChars = Math.max(1, Math.round(frac * width));
          const label = `${seg.start.toFixed(1)}s`;
          const pad = Math.max(0, nChars + seg.state.length + 2 - label.length);
          return (
            <Text key={i} dimColor>
              {label}
              {' '.repeat(pad)}
            </Text>
          );
        })}
        <Text dimColor>{maxT.toFixed(1)}s</Text>
      </Box>
    </Box>
  );
}
