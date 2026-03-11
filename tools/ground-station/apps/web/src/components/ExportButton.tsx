import React, { useCallback } from 'react';
import type { FlightFrame } from '@mpr/shared';
import { framesToCsv } from '@mpr/shared';

interface ExportButtonProps {
  frames: FlightFrame[];
  version: number;
}

export function ExportButton({ frames, version }: ExportButtonProps) {
  const handleExport = useCallback(() => {
    if (!frames.length) return;

    const csv = framesToCsv(frames, version);
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);

    const a = document.createElement('a');
    a.href = url;
    a.download = `flight_export_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [frames, version]);

  return (
    <button className="btn" onClick={handleExport} disabled={!frames.length}>
      Export CSV
    </button>
  );
}
