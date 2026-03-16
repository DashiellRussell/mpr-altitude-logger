import React from 'react';
import { Box, Text } from 'ink';
import { sparkline } from '@mpr/shared';
import type { DiagStats, FlightFrame } from '@mpr/shared';

interface DiagnosticsPanelProps {
  diag: DiagStats;
  frames: FlightFrame[];
  nFrames: number;
}

/** Post-flight diagnostics panel — surfaces v3 health data */
export function DiagnosticsPanel({ diag, frames, nFrames }: DiagnosticsPanelProps) {
  // Frame timing sparkline (subsample to ~60 points)
  const step = Math.max(1, Math.floor(frames.length / 60));
  const frameUsVals = frames.filter((_, i) => i % step === 0).map(f => f.frame_us ?? 0);
  const frameUsSpark = sparkline(frameUsVals, 55);

  // Free KB sparkline
  const freeKbVals = frames.filter((_, i) => i % step === 0).map(f => f.free_kb ?? 0);
  const freeKbSpark = sparkline(freeKbVals, 55);

  // CPU temp sparkline
  const cpuVals = frames.filter((_, i) => i % step === 0).map(f => (f.cpu_temp_c ?? 40) - 40);
  const cpuSpark = sparkline(cpuVals, 55);

  // Crash detection heuristic
  const isCrash = !diag.cleanShutdown && nFrames % 50 === 0;

  return (
    <Box flexDirection="column" marginBottom={1}>
      <Text bold>SYSTEM DIAGNOSTICS {isCrash ? <Text color="red" bold> [PROBABLE CRASH]</Text> : ''}</Text>

      {isCrash && (
        <Text color="red">
          {'  '}Session ended at exactly {nFrames} frames (flush boundary) — likely WDT reset
        </Text>
      )}

      <Text>
        {'  Frame  '}
        <Text color="yellow">{frameUsSpark}</Text>
        {'  '}
        <Text dimColor>avg={diag.frameUs.avg}us  p95={diag.frameUs.p95}us  max={diag.frameUs.max}us</Text>
      </Text>

      <Text>
        {'  Heap   '}
        <Text color={diag.freeKb.trend < -10 ? 'red' : 'green'}>{freeKbSpark}</Text>
        {'  '}
        <Text dimColor>{diag.freeKb.start}KB{'→'}{diag.freeKb.end}KB  min={diag.freeKb.min}KB</Text>
        {diag.freeKb.trend < -10 && <Text color="red">  LEAK {diag.freeKb.trend}KB</Text>}
      </Text>

      <Text>
        {'  Temp   '}
        <Text color={diag.cpuTemp.max > 70 ? 'red' : 'cyan'}>{cpuSpark}</Text>
        {'  '}
        <Text dimColor>avg={diag.cpuTemp.avg}C  max={diag.cpuTemp.max}C</Text>
      </Text>

      {diag.flushUs.count > 0 && (
        <Text>
          {'  Flush  '}
          <Text dimColor>
            avg={diag.flushUs.avg}us  max=
            <Text color={diag.flushUs.max > 50000 ? 'red' : diag.flushUs.max > 20000 ? 'yellow' : 'green'}>
              {diag.flushUs.max}us
            </Text>
            {'  '}({diag.flushUs.count} flushes)
          </Text>
          {diag.flushUs.max > 50000 && <Text color="red">  SD STALL</Text>}
        </Text>
      )}

      <Text>
        {'  Health '}
        <Text color={diag.i2cErrors > 0 ? 'red' : 'green'}>I2C errors: {diag.i2cErrors}</Text>
        {'    '}
        <Text color={diag.overruns > 10 ? 'yellow' : 'green'}>Overruns: {diag.overruns}/{nFrames}</Text>
      </Text>
    </Box>
  );
}
