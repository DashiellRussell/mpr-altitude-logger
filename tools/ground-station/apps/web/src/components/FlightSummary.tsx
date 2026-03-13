import React from 'react';
import type { FlightStats } from '@mpr/shared';

interface FlightSummaryProps {
  stats: FlightStats;
  version: number;
}

function fmt(value: number, decimals: number = 1): string {
  return value.toFixed(decimals);
}

function fmtTime(seconds: number | null): string {
  if (seconds === null) return '--';
  return `T+${seconds.toFixed(2)}s`;
}

function fmtVoltageRange(range: [number, number] | undefined): string {
  if (!range) return '--';
  return `${(range[0] / 1000).toFixed(2)}V - ${(range[1] / 1000).toFixed(2)}V`;
}

export function FlightSummary({ stats, version }: FlightSummaryProps) {
  return (
    <div className="card">
      <h2>Flight Summary</h2>
      <div className="stats-grid">
        <div className="stat-card accent-blue">
          <div className="stat-label">Apogee</div>
          <div className="stat-value">{fmt(stats.maxAlt)} m</div>
          <div className="stat-sub">{fmtTime(stats.maxAltTime)}</div>
        </div>

        <div className="stat-card accent-green">
          <div className="stat-label">Max Velocity</div>
          <div className="stat-value">{fmt(stats.maxVel)} m/s</div>
          <div className="stat-sub">{fmtTime(stats.maxVelTime)}</div>
        </div>

        <div className="stat-card accent-orange">
          <div className="stat-label">Max Acceleration (est)</div>
          <div className="stat-value">{fmt(stats.maxAccel)} m/s2</div>
          <div className="stat-sub">{fmtTime(stats.maxAccelTime)}</div>
        </div>

        <div className="stat-card accent-blue">
          <div className="stat-label">Flight Duration</div>
          <div className="stat-value">{fmt(stats.duration)} s</div>
          <div className="stat-sub">{stats.nFrames} frames</div>
        </div>

        <div className="stat-card accent-cyan">
          <div className="stat-label">Sample Rate</div>
          <div className="stat-value">{fmt(stats.sampleRate, 0)} Hz</div>
        </div>

        <div className="stat-card accent-orange">
          <div className="stat-label">Landing Velocity</div>
          <div className="stat-value">{fmt(Math.abs(stats.landingVel))} m/s</div>
        </div>

        <div className={`stat-card ${stats.hadError ? 'accent-red' : 'accent-green'}`}>
          <div className="stat-label">Error Flag</div>
          <div className="stat-value">{stats.hadError ? 'ERROR' : 'CLEAN'}</div>
        </div>

        {version >= 2 && (
          <>
            <div className="stat-card accent-green">
              <div className="stat-label">3V3 Rail</div>
              <div className="stat-value">{fmtVoltageRange(stats.v3v3Range)}</div>
            </div>
            <div className="stat-card accent-orange">
              <div className="stat-label">5V Rail</div>
              <div className="stat-value">{fmtVoltageRange(stats.v5vRange)}</div>
            </div>
            <div className="stat-card accent-red">
              <div className="stat-label">9V Rail</div>
              <div className="stat-value">{fmtVoltageRange(stats.v9vRange)}</div>
            </div>
          </>
        )}

        {version < 2 && stats.vBattRange && (
          <div className="stat-card accent-red">
            <div className="stat-label">Battery</div>
            <div className="stat-value">{fmtVoltageRange(stats.vBattRange)}</div>
          </div>
        )}
      </div>
    </div>
  );
}
