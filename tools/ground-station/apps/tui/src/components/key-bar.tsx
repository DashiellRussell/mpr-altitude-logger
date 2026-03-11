import React from 'react';
import { Box, Text } from 'ink';

interface KeyBarProps {
  keys: Array<[string, string]>;
  width?: number;
}

/** Bottom hotkey legend spanning full width with separator line */
export function KeyBar({ keys, width = 120 }: KeyBarProps) {
  const sep = '\u2500'.repeat(width);

  return (
    <Box flexDirection="column" marginTop={1}>
      <Text dimColor>{sep}</Text>
      <Box>
        <Text>{'  '}</Text>
        {keys.map(([key, label], i) => (
          <React.Fragment key={key + label}>
            <Text bold color="cyan">[{key}]</Text>
            <Text dimColor> {label}</Text>
            {i < keys.length - 1 ? <Text>{'    '}</Text> : null}
          </React.Fragment>
        ))}
      </Box>
    </Box>
  );
}
