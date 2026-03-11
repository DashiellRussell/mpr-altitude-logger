import React from 'react';
import { Text } from 'ink';
import { sparkline as makeSparkline } from '@mpr/shared';

interface SparklineProps {
  values: number[];
  label?: string;
  prefix?: string;
  width?: number;
  color?: string;
}

/** Unicode sparkline chart with label */
export function Sparkline({ values, label, prefix = 'Alt', width = 48, color = 'cyan' }: SparklineProps) {
  const spark = makeSparkline(values, width);

  return (
    <Text>
      {' '}{prefix}{' '}
      <Text color={color}>{spark}</Text>
      {label ? <Text>{' '}{label}</Text> : null}
    </Text>
  );
}
