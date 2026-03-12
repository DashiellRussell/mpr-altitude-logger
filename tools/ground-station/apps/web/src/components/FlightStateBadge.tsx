import React from 'react';

const STATE_BADGE_COLORS: Record<string, string> = {
  PAD: '#888888',
  BOOST: '#ff4444',
  COAST: '#ffaa00',
  APOGEE: '#44ff44',
  DROGUE: '#44ffff',
  MAIN: '#4488ff',
  LANDED: '#ff44ff',
};

interface FlightStateBadgeProps {
  state: string;
}

export function FlightStateBadge({ state }: FlightStateBadgeProps) {
  const color = STATE_BADGE_COLORS[state] ?? '#888';

  return (
    <span
      className="flight-state-badge"
      style={{
        color,
        borderColor: color,
        boxShadow: `0 0 12px ${color}40, inset 0 0 8px ${color}20`,
      }}
    >
      {state}
    </span>
  );
}
