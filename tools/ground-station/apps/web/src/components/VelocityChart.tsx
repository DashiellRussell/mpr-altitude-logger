import React, { useMemo } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
} from 'recharts';
import type { FlightFrame, StateTransition } from '@mpr/shared';

const STATE_TRANSITION_COLORS: Record<string, string> = {
  BOOST: '#ff4444',
  COAST: '#ffaa00',
  APOGEE: '#44ff44',
  DROGUE: '#44ffff',
  MAIN: '#4488ff',
  LANDED: '#ff44ff',
};

interface VelocityChartProps {
  frames: FlightFrame[];
  transitions: StateTransition[];
}

export function VelocityChart({ frames, transitions }: VelocityChartProps) {
  const data = useMemo(() => {
    if (!frames.length) return [];
    const t0 = frames[0].timestamp_ms;
    return frames.map((f) => ({
      time: parseFloat(((f.timestamp_ms - t0) / 1000).toFixed(3)),
      velocity: parseFloat(f.vel_filtered_ms.toFixed(2)),
    }));
  }, [frames]);

  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3e" />
        <XAxis
          dataKey="time"
          stroke="#666"
          tick={{ fill: '#888', fontSize: 11 }}
          label={{ value: 'Time (s)', position: 'insideBottom', offset: -2, fill: '#888', fontSize: 11 }}
        />
        <YAxis
          stroke="#666"
          tick={{ fill: '#888', fontSize: 11 }}
          label={{ value: 'Velocity (m/s)', angle: -90, position: 'insideLeft', offset: 10, fill: '#888', fontSize: 11 }}
        />
        <Tooltip
          contentStyle={{ background: '#1a1a2e', border: '1px solid #2a2a3e', borderRadius: 6, fontSize: 12 }}
          labelStyle={{ color: '#888' }}
          labelFormatter={(v) => `T+${v}s`}
        />
        <Legend wrapperStyle={{ fontSize: 11 }} />

        <ReferenceLine y={0} stroke="#444" strokeWidth={1} />

        {transitions.map((t, i) => (
          <ReferenceLine
            key={i}
            x={parseFloat(t.time.toFixed(3))}
            stroke={STATE_TRANSITION_COLORS[t.to_state] ?? '#666'}
            strokeDasharray="4 2"
          />
        ))}

        <Line
          type="monotone"
          dataKey="velocity"
          stroke="#4aff4a"
          strokeWidth={2}
          dot={false}
          name="Filtered Velocity"
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
