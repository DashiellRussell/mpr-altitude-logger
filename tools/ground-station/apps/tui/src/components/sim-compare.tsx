import React from 'react';
import { Box, Text } from 'ink';
import type { FlightStats, SimSummary } from '@mpr/shared';
import { suggestCdAdjustment } from '@mpr/shared';

interface SimCompareProps {
  stats: FlightStats;
  sim: SimSummary;
  cdOld?: number;
}

/** Actual vs predicted comparison table with Cd suggestion */
export function SimCompare({ stats, sim, cdOld = 0.45 }: SimCompareProps) {
  const deltaAlt = stats.maxAlt - sim.maxAlt;
  const deltaTApo = stats.maxAltTime - sim.maxAltTime;
  const deltaVel = stats.maxVel - sim.maxVel;
  const deltaDur = stats.duration - sim.duration;

  const altColor =
    Math.abs(deltaAlt) < 50 ? 'green' : Math.abs(deltaAlt) < 100 ? 'yellow' : 'red';
  const velColor =
    Math.abs(deltaVel) < 20 ? 'green' : Math.abs(deltaVel) < 50 ? 'yellow' : 'red';

  const { newCd, direction } = suggestCdAdjustment(stats.maxAlt, sim.maxAlt, cdOld);

  const rows = [
    {
      label: 'Apogee',
      actual: `${stats.maxAlt.toFixed(1)} m`,
      predicted: `${sim.maxAlt.toFixed(1)} m`,
      delta: `${deltaAlt >= 0 ? '+' : ''}${deltaAlt.toFixed(1)} m`,
      deltaColor: altColor,
    },
    {
      label: 'Time to Apogee',
      actual: `${stats.maxAltTime.toFixed(1)} s`,
      predicted: `${sim.maxAltTime.toFixed(1)} s`,
      delta: `${deltaTApo >= 0 ? '+' : ''}${deltaTApo.toFixed(1)} s`,
      deltaColor: 'white' as const,
    },
    {
      label: 'Max Velocity',
      actual: `${stats.maxVel.toFixed(1)} m/s`,
      predicted: `${sim.maxVel.toFixed(1)} m/s`,
      delta: `${deltaVel >= 0 ? '+' : ''}${deltaVel.toFixed(1)} m/s`,
      deltaColor: velColor,
    },
    {
      label: 'Flight Duration',
      actual: `${stats.duration.toFixed(1)} s`,
      predicted: `${sim.duration.toFixed(1)} s`,
      delta: `${deltaDur >= 0 ? '+' : ''}${deltaDur.toFixed(1)} s`,
      deltaColor: 'white' as const,
    },
  ];

  return (
    <Box flexDirection="column" marginBottom={1}>
      <Text bold>ACTUAL vs PREDICTED</Text>
      <Text>
        {'  '}
        {''.padEnd(20)}
        {'Actual'.padStart(12)}
        {'Predicted'.padStart(12)}
        {'Delta'.padStart(12)}
      </Text>
      {rows.map((row) => (
        <Text key={row.label}>
          {'  '}
          <Text bold>{row.label.padEnd(20)}</Text>
          {row.actual.padStart(12)}
          {row.predicted.padStart(12)}
          {'  '}
          <Text color={row.deltaColor as any}>{row.delta.padStart(10)}</Text>
        </Text>
      ))}
      {direction !== 'none' && (
        <Text>
          {'\n  Suggested Cd adjustment: '}
          {direction}
          {' from '}
          {cdOld.toFixed(2)}
          {' to ~'}
          {newCd.toFixed(3)}
        </Text>
      )}
    </Box>
  );
}
