import React from 'react';

interface TelemetryReadoutProps {
  label: string;
  value: string;
  unit?: string;
  color?: string;
}

export function TelemetryReadout({ label, value, unit, color = '#e0e0e0' }: TelemetryReadoutProps) {
  return (
    <div className="telemetry-readout">
      <div className="telemetry-label">{label}</div>
      <div className="telemetry-value" style={{ color }}>
        {value}
        {unit && <span className="telemetry-unit">{unit}</span>}
      </div>
    </div>
  );
}
