import React from 'react';
import { Box, Text } from 'ink';

interface HeaderProps {
  title: string;
  width?: number;
}

const ROCKET_ART = '\u25b2';

/** Full-width UNSW ROCKETRY banner */
export function Header({ title, width = 120 }: HeaderProps) {
  const topBot = '\u2550'.repeat(width);
  const label = `${ROCKET_ART}  UNSW ROCKETRY \u2014 ${title}`;
  const pad = width - label.length - 2;

  return (
    <Box flexDirection="column" marginBottom={1}>
      <Text color="blue">{'\u2554' + topBot + '\u2557'}</Text>
      <Text color="blue">
        {'\u2551  '}
        <Text bold color="white">{label}</Text>
        {' '.repeat(Math.max(0, pad))}
        {'\u2551'}
      </Text>
      <Text color="blue">{'\u255a' + topBot + '\u255d'}</Text>
    </Box>
  );
}
