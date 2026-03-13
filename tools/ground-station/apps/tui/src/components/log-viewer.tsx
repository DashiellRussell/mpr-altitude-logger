import React, { useState } from 'react';
import { Box, Text, useInput } from 'ink';
import type { FlightFrame } from '@mpr/shared';
import { STATE_NAMES, STATE_COLORS } from '@mpr/shared';

interface LogViewerProps {
  frames: FlightFrame[];
  version: number;
  onClose: () => void;
}

type InkColor = 'white' | 'red' | 'yellow' | 'green' | 'cyan' | 'blue' | 'magenta' | 'gray';

function toInkColor(color: string): InkColor {
  const valid: InkColor[] = ['white', 'red', 'yellow', 'green', 'cyan', 'blue', 'magenta', 'gray'];
  return valid.includes(color as InkColor) ? (color as InkColor) : 'cyan';
}

const PAGE_SIZE = 30;
const HEADER = '  #     T(ms)     State   P(Pa)      T(°C)  Alt Raw  Alt Filt  Vel(m/s)  3V3   5V    9V   Flg';
const DIVIDER = '─'.repeat(HEADER.length + 2);

export function LogViewer({ frames, version, onClose }: LogViewerProps) {
  const [offset, setOffset] = useState(0);

  useInput((input, key) => {
    if (input === 'q' || input === 'Q' || input === 'l' || input === 'L' || key.escape) {
      onClose();
    }
    if (key.downArrow) setOffset(o => Math.min(o + 1, Math.max(0, frames.length - PAGE_SIZE)));
    if (key.upArrow) setOffset(o => Math.max(o - 1, 0));
    if (key.pageDown || input === ' ') setOffset(o => Math.min(o + PAGE_SIZE, Math.max(0, frames.length - PAGE_SIZE)));
    if (key.pageUp) setOffset(o => Math.max(o - PAGE_SIZE, 0));
    if (input === 'g') setOffset(0);
    if (input === 'G') setOffset(Math.max(0, frames.length - PAGE_SIZE));
  });

  const visible = frames.slice(offset, offset + PAGE_SIZE);
  const t0 = frames.length > 0 ? frames[0].timestamp_ms : 0;

  return (
    <Box flexDirection="column">
      <Box justifyContent="space-between">
        <Text bold>FLIGHT LOG — {frames.length.toLocaleString()} frames (v{version})</Text>
        <Text dimColor>
          {offset + 1}–{Math.min(offset + PAGE_SIZE, frames.length)} of {frames.length}
        </Text>
      </Box>
      <Text dimColor>{DIVIDER}</Text>
      <Text bold dimColor>{HEADER}</Text>
      <Text dimColor>{DIVIDER}</Text>
      {visible.map((f, i) => {
        const idx = offset + i;
        const stateName = STATE_NAMES[f.state] ?? '?';
        const stateColor = toInkColor(STATE_COLORS[stateName] ?? 'cyan');
        const t = f.timestamp_ms - t0;
        const hasError = (f.flags & 0x08) !== 0;

        return (
          <Text key={idx}>
            <Text dimColor>{String(idx).padStart(5)}</Text>
            {'  '}
            <Text>{String(f.timestamp_ms).padStart(8)}</Text>
            {'  '}
            <Text color={stateColor} bold>{stateName.padEnd(7)}</Text>
            {' '}
            <Text>{f.pressure_pa.toFixed(0).padStart(8)}</Text>
            {'  '}
            <Text>{f.temperature_c.toFixed(1).padStart(6)}</Text>
            {'  '}
            <Text>{f.alt_raw_m.toFixed(2).padStart(7)}</Text>
            {'  '}
            <Text color="cyan">{f.alt_filtered_m.toFixed(2).padStart(8)}</Text>
            {'  '}
            <Text color={f.vel_filtered_ms > 0 ? 'green' : f.vel_filtered_ms < -2 ? 'red' : 'white'}>
              {((f.vel_filtered_ms >= 0 ? '+' : '') + f.vel_filtered_ms.toFixed(2)).padStart(8)}
            </Text>
            {'  '}
            {version >= 2 ? (
              <>
                <Text color={(f.v_3v3_mv ?? 0) < 3000 ? 'red' : undefined} dimColor={(f.v_3v3_mv ?? 0) >= 3000}>
                  {String(f.v_3v3_mv ?? 0).padStart(4)}
                </Text>
                {'  '}
                <Text color={(f.v_5v_mv ?? 0) < 4500 ? 'red' : undefined} dimColor={(f.v_5v_mv ?? 0) >= 4500}>
                  {String(f.v_5v_mv ?? 0).padStart(4)}
                </Text>
                {'  '}
                <Text color={(f.v_9v_mv ?? 0) < 8000 ? 'red' : undefined} dimColor={(f.v_9v_mv ?? 0) >= 8000}>
                  {String(f.v_9v_mv ?? 0).padStart(4)}
                </Text>
              </>
            ) : (
              <Text color={(f.v_batt_mv ?? 0) < 3000 ? 'red' : undefined} dimColor={(f.v_batt_mv ?? 0) >= 3000}>
                {String(f.v_batt_mv ?? 0).padStart(4)}{'          '}
              </Text>
            )}
            {'  '}
            <Text color={hasError ? 'red' : 'gray'}>
              {hasError ? 'ERR' : f.flags > 0 ? `0x${f.flags.toString(16).padStart(2, '0')}` : '  ·'}
            </Text>
          </Text>
        );
      })}
      <Text dimColor>{DIVIDER}</Text>
      <Text dimColor>
        {'  '}↑↓ Scroll{'  '}PgUp/PgDn Page{'  '}g/G Start/End{'  '}L/Q/Esc Close
      </Text>
    </Box>
  );
}
