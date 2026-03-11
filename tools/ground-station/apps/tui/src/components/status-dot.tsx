import React from 'react';
import { Text } from 'ink';

interface StatusDotProps {
  connected: boolean;
  port: string | null;
  error?: string | null;
}

/** Connection status indicator: green/red dot with port or error */
export function StatusDot({ connected, port, error }: StatusDotProps) {
  if (connected) {
    return (
      <Text>
        {' '}
        <Text color="green">{'\u25cf'}</Text>
        {' Connected'}
        {port ? <Text dimColor>{'  '}{port}</Text> : null}
      </Text>
    );
  }

  return (
    <Text>
      {' '}
      <Text color="red">{'\u25cf'}</Text>
      {' '}
      <Text color="red">{error ?? 'Disconnected'}</Text>
    </Text>
  );
}
