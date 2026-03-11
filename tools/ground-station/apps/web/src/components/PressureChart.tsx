import React, { useMemo } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import type { FlightFrame } from '@mpr/shared';

interface PressureChartProps {
  frames: FlightFrame[];
}

export function PressureChart({ frames }: PressureChartProps) {
  const data = useMemo(() => {
    if (!frames.length) return [];
    const t0 = frames[0].timestamp_ms;
    return frames.map((f) => ({
      time: parseFloat(((f.timestamp_ms - t0) / 1000).toFixed(3)),
      pressure: parseFloat((f.pressure_pa / 100).toFixed(2)),
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
          domain={['auto', 'auto']}
          label={{ value: 'Pressure (hPa)', angle: -90, position: 'insideLeft', offset: 10, fill: '#888', fontSize: 11 }}
        />
        <Tooltip
          contentStyle={{ background: '#1a1a2e', border: '1px solid #2a2a3e', borderRadius: 6, fontSize: 12 }}
          labelStyle={{ color: '#888' }}
          labelFormatter={(v) => `T+${v}s`}
        />
        <Legend wrapperStyle={{ fontSize: 11 }} />

        <Line
          type="monotone"
          dataKey="pressure"
          stroke="#ffaa00"
          strokeWidth={2}
          dot={false}
          name="Pressure (hPa)"
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
