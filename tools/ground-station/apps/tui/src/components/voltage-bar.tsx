import React from 'react';
import { Box, Text } from 'ink';
import { voltageBar, RAIL_SPECS } from '@mpr/shared';

interface VoltageBarProps {
  label: string;
  value: number;
  rail: '3V3' | '5V' | '9V';
  barWidth?: number;
}

/** Colored voltage bar with nominal, value, and status */
export function VoltageBar({ label, value, rail, barWidth = 24 }: VoltageBarProps) {
  const spec = RAIL_SPECS[rail];
  const { bar, color, status } = voltageBar(value, spec.nominal, spec.min, spec.max, barWidth);

  const colorName = color as 'red' | 'yellow' | 'green';

  return (
    <Text>
      {' '}{label.padEnd(4)}
      <Text color={colorName}>{bar}</Text>
      {'  '}
      <Text color={colorName} bold>{value.toFixed(2)}V</Text>
      <Text dimColor>{'  '}{spec.min.toFixed(1)}-{spec.max.toFixed(1)}V</Text>
      {'  '}{status === 'WARN' ? <Text color="red" bold>WARN</Text> : <Text color="green">OK</Text>}
    </Text>
  );
}
