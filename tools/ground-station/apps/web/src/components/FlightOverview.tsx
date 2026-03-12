import React, { useMemo } from 'react';
import {
  ComposedChart,
  Line,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
} from 'recharts';
import type { FlightFrame, SimSummary, StateTransition } from '@mpr/shared';

const STATE_COLORS: Record<string, string> = {
  BOOST: '#ff4444',
  COAST: '#ffaa00',
  APOGEE: '#44ff44',
  DROGUE: '#44ffff',
  MAIN: '#4488ff',
  LANDED: '#ff44ff',
};

interface FlightOverviewProps {
  frames: FlightFrame[];
  simSummary?: SimSummary;
  transitions: StateTransition[];
  version: number;
  cursorTime?: number;
}

interface OverviewPoint {
  time: number;
  altFiltered: number;
  altRaw: number;
  velocity: number;
  pressure: number;
  simAlt?: number;
  simVel?: number;
}

export function FlightOverview({ frames, simSummary, transitions, version, cursorTime }: FlightOverviewProps) {
  const data = useMemo(() => {
    if (!frames.length) return [];
    const t0 = frames[0].timestamp_ms;

    // Downsample if too many frames for performance (keep every Nth)
    const maxPoints = 1500;
    const step = Math.max(1, Math.floor(frames.length / maxPoints));

    const points: OverviewPoint[] = [];
    for (let i = 0; i < frames.length; i += step) {
      const f = frames[i];
      points.push({
        time: parseFloat(((f.timestamp_ms - t0) / 1000).toFixed(3)),
        altFiltered: parseFloat(f.alt_filtered_m.toFixed(2)),
        altRaw: parseFloat(f.alt_raw_m.toFixed(2)),
        velocity: parseFloat(f.vel_filtered_ms.toFixed(2)),
        pressure: parseFloat((f.pressure_pa / 100).toFixed(2)),
      });
    }

    // Overlay sim data — align sim T=0 to launch (BOOST)
    if (simSummary && simSummary.times.length > 1) {
      const simTimes = simSummary.times;
      const simAlts = simSummary.altitudes;
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
          point.simAlt = parseFloat((simAlts[lo] + frac * (simAlts[hi] - simAlts[lo])).toFixed(2));
          point.simVel = parseFloat((simVels[lo] + frac * (simVels[hi] - simVels[lo])).toFixed(2));
        }
      }
    }

    return points;
  }, [frames, simSummary]);

  const tooltipFormatter = (value: number, name: string) => {
    const units: Record<string, string> = {
      'Altitude (filtered)': 'm',
      'Altitude (raw)': 'm',
      'Velocity': 'm/s',
      'Pressure': 'hPa',
      'Sim Altitude': 'm',
      'Sim Velocity': 'm/s',
    };
    return [`${value.toFixed(1)} ${units[name] ?? ''}`, name];
  };

  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={data} margin={{ top: 5, right: 60, bottom: 5, left: 10 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3e" />
        <XAxis
          dataKey="time"
          stroke="#666"
          tick={{ fill: '#888', fontSize: 11 }}
          label={{ value: 'Time (s)', position: 'insideBottom', offset: -2, fill: '#888', fontSize: 11 }}
        />
        {/* Left Y: Altitude */}
        <YAxis
          yAxisId="alt"
          stroke="#4a9eff"
          tick={{ fill: '#4a9eff', fontSize: 10 }}
          label={{ value: 'Altitude (m)', angle: -90, position: 'insideLeft', offset: 10, fill: '#4a9eff', fontSize: 10 }}
        />
        {/* Right Y: Velocity */}
        <YAxis
          yAxisId="vel"
          orientation="right"
          stroke="#4aff4a"
          tick={{ fill: '#4aff4a', fontSize: 10 }}
          label={{ value: 'Velocity (m/s)', angle: 90, position: 'insideRight', offset: 10, fill: '#4aff4a', fontSize: 10 }}
        />

        <Tooltip
          contentStyle={{ background: '#1a1a2e', border: '1px solid #2a2a3e', borderRadius: 6, fontSize: 12 }}
          labelStyle={{ color: '#888' }}
          labelFormatter={(v) => `T+${v}s`}
          formatter={tooltipFormatter}
        />
        <Legend wrapperStyle={{ fontSize: 11 }} />

        {transitions.map((t, i) => (
          <ReferenceLine
            key={i}
            yAxisId="alt"
            x={parseFloat(t.time.toFixed(3))}
            stroke={STATE_COLORS[t.to_state] ?? '#666'}
            strokeDasharray="4 2"
            label={{
              value: t.to_state,
              position: 'top',
              fill: STATE_COLORS[t.to_state] ?? '#888',
              fontSize: 9,
            }}
          />
        ))}

        {/* Raw altitude as faint area */}
        <Area
          yAxisId="alt"
          type="monotone"
          dataKey="altRaw"
          stroke="none"
          fill="#4a9eff"
          fillOpacity={0.08}
          name="Altitude (raw)"
        />

        {/* Filtered altitude */}
        <Line
          yAxisId="alt"
          type="monotone"
          dataKey="altFiltered"
          stroke="#4a9eff"
          strokeWidth={2}
          dot={false}
          name="Altitude (filtered)"
        />

        {/* Sim altitude */}
        {simSummary && (
          <Line
            yAxisId="alt"
            type="monotone"
            dataKey="simAlt"
            stroke="#ff4a4a"
            strokeDasharray="6 3"
            strokeWidth={1.5}
            dot={false}
            name="Sim Altitude"
            connectNulls={false}
          />
        )}

        {/* Velocity */}
        <Line
          yAxisId="vel"
          type="monotone"
          dataKey="velocity"
          stroke="#4aff4a"
          strokeWidth={1.5}
          dot={false}
          name="Velocity"
        />

        {/* Sim velocity */}
        {simSummary && (
          <Line
            yAxisId="vel"
            type="monotone"
            dataKey="simVel"
            stroke="#ff8844"
            strokeDasharray="6 3"
            strokeWidth={1.5}
            dot={false}
            name="Sim Velocity"
            connectNulls={false}
          />
        )}
        {cursorTime !== undefined && (
          <ReferenceLine
            yAxisId="alt"
            x={parseFloat(cursorTime.toFixed(3))}
            stroke="#ffffff"
            strokeWidth={1.5}
          />
        )}
      </ComposedChart>
    </ResponsiveContainer>
  );
}
