import React, { useState, useEffect } from 'react';
import { Box, Text } from 'ink';

type LedState = 'blink' | 'solid-error' | 'solid-ready' | 'off';

interface LedIndicatorProps {
  state: LedState;
}

/**
 * Animated LED indicator that mirrors the physical onboard LED.
 * - blink: green circle alternating bright/dim (matches 1s blink on board)
 * - solid-error: solid red circle
 * - solid-ready: solid green circle
 * - off: dim circle
 */
export function LedIndicator({ state }: LedIndicatorProps) {
  const [on, setOn] = useState(true);

  useEffect(() => {
    if (state !== 'blink') {
      setOn(true);
      return;
    }
    const interval = setInterval(() => setOn((v) => !v), 500);
    return () => clearInterval(interval);
  }, [state]);

  let dot: React.ReactNode;
  let label: string;

  switch (state) {
    case 'blink':
      dot = on
        ? <Text color="green" bold>{'\u25cf'}</Text>
        : <Text dimColor>{'\u25cb'}</Text>;
      label = 'BLINKING \u2014 healthy';
      break;
    case 'solid-error':
      dot = <Text color="red" bold>{'\u25cf'}</Text>;
      label = 'SOLID \u2014 ERROR';
      break;
    case 'solid-ready':
      dot = <Text color="green" bold>{'\u25cf'}</Text>;
      label = 'READY \u2014 PAD';
      break;
    case 'off':
    default:
      dot = <Text dimColor>{'\u25cb'}</Text>;
      label = 'OFF';
      break;
  }

  return (
    <Box>
      <Text> LED  </Text>
      {dot}
      <Text> </Text>
      <Text dimColor>{label}</Text>
    </Box>
  );
}
