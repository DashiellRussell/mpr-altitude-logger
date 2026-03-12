import React from 'react';
import type { FlightFrame } from '@mpr/shared';
import { TelemetryReadout } from './TelemetryReadout';
import { FlightStateBadge } from './FlightStateBadge';

interface TelemetryPanelProps {
  frame: FlightFrame | null;
  maxAlt: number;
  currentTime: number;
}

export function TelemetryPanel({ frame, maxAlt, currentTime }: TelemetryPanelProps) {
  if (!frame) {
    return (
      <div className="telemetry-panel">
        <div className="telemetry-empty">No data</div>
      </div>
    );
  }

  const alt = frame.alt_filtered_m;
  const vel = frame.vel_filtered_ms;
  const pressure = frame.pressure_pa / 100; // hPa
  const temp = frame.temperature_c;

  return (
    <div className="telemetry-panel">
      <div className="telemetry-header">
        <FlightStateBadge state={frame.state_name} />
        <span className="telemetry-time">T+{currentTime.toFixed(1)}s</span>
      </div>
      <div className="telemetry-grid">
        <TelemetryReadout
          label="ALTITUDE"
          value={alt.toFixed(1)}
          unit="m"
          color="#4a9eff"
        />
        <TelemetryReadout
          label="VELOCITY"
          value={vel.toFixed(1)}
          unit="m/s"
          color="#4aff4a"
        />
        <TelemetryReadout
          label="MAX ALT"
          value={maxAlt.toFixed(1)}
          unit="m"
          color="#4a9eff"
        />
        <TelemetryReadout
          label="PRESSURE"
          value={pressure.toFixed(1)}
          unit="hPa"
          color="#ffaa00"
        />
        <TelemetryReadout
          label="TEMP"
          value={temp.toFixed(1)}
          unit="C"
          color="#ff8844"
        />
      </div>
    </div>
  );
}
