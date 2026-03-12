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
import type { FlightFrame } from '@mpr/shared';

interface PowerChartProps {
  frames: FlightFrame[];
  version: number;
  cursorTime?: number;
}

export function PowerChart({ frames, version, cursorTime }: PowerChartProps) {
  const data = useMemo(() => {
    if (!frames.length) return [];
    const t0 = frames[0].timestamp_ms;

    if (version >= 2) {
      return frames.map((f) => ({
        time: parseFloat(((f.timestamp_ms - t0) / 1000).toFixed(3)),
        v3v3: parseFloat(((f.v_3v3_mv ?? 0) / 1000).toFixed(3)),
        v5v: parseFloat(((f.v_5v_mv ?? 0) / 1000).toFixed(3)),
        v9v: parseFloat(((f.v_9v_mv ?? 0) / 1000).toFixed(3)),
      }));
    }

    return frames.map((f) => ({
      time: parseFloat(((f.timestamp_ms - t0) / 1000).toFixed(3)),
      vbatt: parseFloat(((f.v_batt_mv ?? 0) / 1000).toFixed(3)),
    }));
  }, [frames, version]);

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
          label={{ value: 'Voltage (V)', angle: -90, position: 'insideLeft', offset: 10, fill: '#888', fontSize: 11 }}
        />
        <Tooltip
          contentStyle={{ background: '#1a1a2e', border: '1px solid #2a2a3e', borderRadius: 6, fontSize: 12 }}
          labelStyle={{ color: '#888' }}
          labelFormatter={(v) => `T+${v}s`}
        />
        <Legend wrapperStyle={{ fontSize: 11 }} />

        {version >= 2 ? (
          <>
            <ReferenceLine y={3.3} stroke="#4aff4a" strokeDasharray="8 4" strokeWidth={0.5} />
            <ReferenceLine y={5.0} stroke="#ffaa00" strokeDasharray="8 4" strokeWidth={0.5} />
            <ReferenceLine y={9.0} stroke="#ff4a4a" strokeDasharray="8 4" strokeWidth={0.5} />
            <Line
              type="monotone"
              dataKey="v3v3"
              stroke="#4aff4a"
              strokeWidth={1.5}
              dot={false}
              name="3V3 Rail"
            />
            <Line
              type="monotone"
              dataKey="v5v"
              stroke="#ffaa00"
              strokeWidth={1.5}
              dot={false}
              name="5V Rail"
            />
            <Line
              type="monotone"
              dataKey="v9v"
              stroke="#ff4a4a"
              strokeWidth={1.5}
              dot={false}
              name="9V Rail"
            />
          </>
        ) : (
          <Line
            type="monotone"
            dataKey="vbatt"
            stroke="#ff4a4a"
            strokeWidth={2}
            dot={false}
            name="Battery"
          />
        )}

        {cursorTime !== undefined && (
          <ReferenceLine
            x={parseFloat(cursorTime.toFixed(3))}
            stroke="#ffffff"
            strokeWidth={1.5}
          />
        )}
      </LineChart>
    </ResponsiveContainer>
  );
}
