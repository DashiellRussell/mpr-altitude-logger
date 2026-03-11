import React, { useMemo } from 'react';
import type { FlightStats, SimSummary } from '@mpr/shared';
import { suggestCdAdjustment } from '@mpr/shared';

interface SimComparisonProps {
  stats: FlightStats;
  simSummary: SimSummary;
}

interface ComparisonRow {
  label: string;
  actual: string;
  predicted: string;
  delta: string;
  deltaClass: string;
}

function deltaClass(actual: number, predicted: number, tolerance: number): string {
  if (predicted === 0) return '';
  const pct = Math.abs((actual - predicted) / predicted) * 100;
  if (pct < tolerance) return 'delta-good';
  if (pct < tolerance * 3) return 'delta-warn';
  return 'delta-bad';
}

function fmtDelta(actual: number, predicted: number): string {
  if (predicted === 0) return '--';
  const diff = actual - predicted;
  const pct = (diff / predicted) * 100;
  const sign = diff >= 0 ? '+' : '';
  return `${sign}${diff.toFixed(1)} (${sign}${pct.toFixed(1)}%)`;
}

export function SimComparison({ stats, simSummary }: SimComparisonProps) {
  const rows = useMemo((): ComparisonRow[] => {
    return [
      {
        label: 'Apogee (m)',
        actual: stats.maxAlt.toFixed(1),
        predicted: simSummary.maxAlt.toFixed(1),
        delta: fmtDelta(stats.maxAlt, simSummary.maxAlt),
        deltaClass: deltaClass(stats.maxAlt, simSummary.maxAlt, 5),
      },
      {
        label: 'Time to Apogee (s)',
        actual: stats.maxAltTime.toFixed(2),
        predicted: simSummary.maxAltTime.toFixed(2),
        delta: fmtDelta(stats.maxAltTime, simSummary.maxAltTime),
        deltaClass: deltaClass(stats.maxAltTime, simSummary.maxAltTime, 5),
      },
      {
        label: 'Max Velocity (m/s)',
        actual: stats.maxVel.toFixed(1),
        predicted: simSummary.maxVel.toFixed(1),
        delta: fmtDelta(stats.maxVel, simSummary.maxVel),
        deltaClass: deltaClass(stats.maxVel, simSummary.maxVel, 10),
      },
      {
        label: 'Flight Duration (s)',
        actual: stats.duration.toFixed(1),
        predicted: simSummary.duration.toFixed(1),
        delta: fmtDelta(stats.duration, simSummary.duration),
        deltaClass: deltaClass(stats.duration, simSummary.duration, 10),
      },
    ];
  }, [stats, simSummary]);

  const cdSuggestion = useMemo(() => {
    return suggestCdAdjustment(stats.maxAlt, simSummary.maxAlt);
  }, [stats.maxAlt, simSummary.maxAlt]);

  return (
    <div className="card">
      <h2>Actual vs Simulation</h2>
      <table className="comparison-table">
        <thead>
          <tr>
            <th>Metric</th>
            <th>Actual</th>
            <th>Predicted</th>
            <th>Delta</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              <td>{row.label}</td>
              <td>{row.actual}</td>
              <td>{row.predicted}</td>
              <td className={row.deltaClass}>{row.delta}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {cdSuggestion.direction !== 'none' && (
        <div className="cd-suggestion">
          Cd adjustment suggestion: {cdSuggestion.direction} Cd from 0.45 to{' '}
          <strong>{cdSuggestion.newCd.toFixed(3)}</strong>
          {' '}to match actual apogee.
        </div>
      )}
    </div>
  );
}
