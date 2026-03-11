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
import type { FlightFrame, SimSummary, StateTransition } from '@mpr/shared';

const STATE_TRANSITION_COLORS: Record<string, string> = {
  BOOST: '#ff4444',
  COAST: '#ffaa00',
  APOGEE: '#44ff44',
  DROGUE: '#44ffff',
  MAIN: '#4488ff',
  LANDED: '#ff44ff',
};

interface AltitudeChartProps {
  frames: FlightFrame[];
  simSummary?: SimSummary;
  transitions: StateTransition[];
}

interface ChartPoint {
  time: number;
  filtered: number;
  raw: number;
  sim?: number;
}

export function AltitudeChart({ frames, simSummary, transitions }: AltitudeChartProps) {
  const data = useMemo(() => {
    if (!frames.length) return [];
    const t0 = frames[0].timestamp_ms;

    const points: ChartPoint[] = frames.map((f) => ({
      time: parseFloat(((f.timestamp_ms - t0) / 1000).toFixed(3)),
      filtered: parseFloat(f.alt_filtered_m.toFixed(2)),
      raw: parseFloat(f.alt_raw_m.toFixed(2)),
    }));

    // Overlay sim data by interpolation
    if (simSummary && simSummary.times.length > 1) {
      const simTimes = simSummary.times;
      const simAlts = simSummary.altitudes;

      for (const point of points) {
        const t = point.time;
        if (t < simTimes[0] || t > simTimes[simTimes.length - 1]) continue;

        // Binary search for bracket
        let lo = 0;
        let hi = simTimes.length - 1;
        while (lo < hi - 1) {
          const mid = (lo + hi) >> 1;
          if (simTimes[mid] <= t) lo = mid;
          else hi = mid;
        }
        // Linear interpolation
        const dt = simTimes[hi] - simTimes[lo];
        if (dt > 0) {
          const frac = (t - simTimes[lo]) / dt;
          point.sim = parseFloat((simAlts[lo] + frac * (simAlts[hi] - simAlts[lo])).toFixed(2));
        }
      }
    }

    return points;
  }, [frames, simSummary]);

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
          label={{ value: 'Altitude (m)', angle: -90, position: 'insideLeft', offset: 10, fill: '#888', fontSize: 11 }}
        />
        <Tooltip
          contentStyle={{ background: '#1a1a2e', border: '1px solid #2a2a3e', borderRadius: 6, fontSize: 12 }}
          labelStyle={{ color: '#888' }}
          itemStyle={{ padding: 2 }}
          labelFormatter={(v) => `T+${v}s`}
        />
        <Legend wrapperStyle={{ fontSize: 11 }} />

        {transitions.map((t, i) => (
          <ReferenceLine
            key={i}
            x={parseFloat(t.time.toFixed(3))}
            stroke={STATE_TRANSITION_COLORS[t.to_state] ?? '#666'}
            strokeDasharray="4 2"
            label={{
              value: t.to_state,
              position: 'top',
              fill: STATE_TRANSITION_COLORS[t.to_state] ?? '#888',
              fontSize: 10,
            }}
          />
        ))}

        <Line
          type="monotone"
          dataKey="raw"
          stroke="#888"
          strokeDasharray="4 2"
          strokeWidth={1}
          dot={false}
          opacity={0.5}
          name="Raw Altitude"
        />
        <Line
          type="monotone"
          dataKey="filtered"
          stroke="#4a9eff"
          strokeWidth={2}
          dot={false}
          name="Filtered Altitude"
        />
        {simSummary && (
          <Line
            type="monotone"
            dataKey="sim"
            stroke="#ff4a4a"
            strokeDasharray="6 3"
            strokeWidth={1.5}
            dot={false}
            name="Simulation"
            connectNulls={false}
          />
        )}
      </LineChart>
    </ResponsiveContainer>
  );
}
