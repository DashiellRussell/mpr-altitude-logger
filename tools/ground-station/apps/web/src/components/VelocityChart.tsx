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

interface VelocityChartProps {
  frames: FlightFrame[];
  transitions: StateTransition[];
  simSummary?: SimSummary;
  cursorTime?: number;
}

export function VelocityChart({ frames, transitions, simSummary, cursorTime }: VelocityChartProps) {
  const data = useMemo(() => {
    if (!frames.length) return [];
    const t0 = frames[0].timestamp_ms;
    const points = frames.map((f) => ({
      time: parseFloat(((f.timestamp_ms - t0) / 1000).toFixed(3)),
      velocity: parseFloat(f.vel_filtered_ms.toFixed(2)),
      sim: undefined as number | undefined,
    }));

    // Align sim T=0 to launch (BOOST) in the log
    if (simSummary && simSummary.times.length > 1) {
      const simTimes = simSummary.times;
      const simVels = simSummary.velocities;

      const boostTransition = transitions.find((tr) => tr.to_state === 'BOOST');
      const launchOffset = boostTransition ? boostTransition.time : 0;

      for (const point of points) {
        const simT = point.time - launchOffset;
        if (simT < simTimes[0] || simT > simTimes[simTimes.length - 1]) continue;
        let lo = 0;
        let hi = simTimes.length - 1;
        while (lo < hi - 1) {
          const mid = (lo + hi) >> 1;
          if (simTimes[mid] <= simT) lo = mid;
          else hi = mid;
        }
        const dt = simTimes[hi] - simTimes[lo];
        if (dt > 0) {
          const frac = (simT - simTimes[lo]) / dt;
          point.sim = parseFloat((simVels[lo] + frac * (simVels[hi] - simVels[lo])).toFixed(2));
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
