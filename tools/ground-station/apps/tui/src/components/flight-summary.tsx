import React from 'react';
import { Box, Text } from 'ink';
import type { FlightStats } from '@mpr/shared';

interface FlightSummaryProps {
  stats: FlightStats;
}

/** Flight summary statistics table */
export function FlightSummary({ stats }: FlightSummaryProps) {
  return (
    <Box flexDirection="column" marginBottom={1}>
      <Text bold>FLIGHT SUMMARY</Text>
      <Text>
        {'  '}
        <Text bold>Apogee       </Text>
        <Text color="cyan">{stats.maxAlt.toFixed(1).padStart(7)} m AGL</Text>
        {'    @ T+'}
        {stats.maxAltTime.toFixed(2)}s
      </Text>
      <Text>
        {'  '}
        <Text bold>Max Velocity </Text>
        <Text color="cyan">{stats.maxVel.toFixed(1).padStart(7)} m/s</Text>
        {'      @ T+'}
        {stats.maxVelTime.toFixed(2)}s
      </Text>
      <Text>
        {'  '}
        <Text bold>Max Accel    </Text>
        <Text color="cyan">~{stats.maxAccel.toFixed(1).padStart(6)} m/s^2</Text>
        {'    (estimated from velocity)'}
      </Text>
      <Text>
        {'  '}
        <Text bold>Flight Time  </Text>
        <Text color="cyan">{stats.duration.toFixed(1).padStart(7)} s</Text>
        {'        (launch to landing)'}
      </Text>
      <Text>
        {'  '}
        <Text bold>Sample Rate  </Text>
        <Text color="cyan">{stats.sampleRate.toFixed(1).padStart(7)} Hz</Text>
        {'      ('}
        {stats.nFrames.toLocaleString()}
        {' frames)'}
      </Text>
      <Text>
        {'  '}
        <Text bold>Landing Vel  </Text>
        <Text color="cyan">{stats.landingVel.toFixed(1).padStart(7)} m/s</Text>
      </Text>
      <Text> </Text>
      {stats.hadError && (
        <Text>
          {'  '}
          <Text color="red">* ERROR flag detected</Text>
        </Text>
      )}
    </Box>
  );
}
