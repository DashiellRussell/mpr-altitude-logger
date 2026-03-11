import type { SimRow } from './types.js';

/**
 * Parse a simulation CSV file (output of simulate.py or openrocket_import.py).
 *
 * Expected columns: time_s, altitude_m, velocity_ms, ...
 * All numeric columns are parsed as floats; non-numeric values are kept as strings.
 */
export function parseSimCsv(text: string): SimRow[] {
  const lines = text.trim().split('\n');
  if (lines.length < 2) return [];

  const headers = lines[0].split(',').map((h) => h.trim());

  const rows: SimRow[] = [];
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;

    const values = line.split(',');
    const row: Record<string, number | string | undefined> = {};

    for (let j = 0; j < headers.length; j++) {
      const key = headers[j];
      const val = values[j]?.trim();
      if (val === undefined || val === '') {
        row[key] = undefined;
      } else {
        const num = parseFloat(val);
        row[key] = isNaN(num) ? val : num;
      }
    }

    // Require at minimum time_s and altitude_m
    if (typeof row.time_s !== 'number') continue;

    rows.push(row as SimRow);
  }

  return rows;
}

/**
 * Detect whether a CSV string is an OpenRocket export (has # comment lines)
 * vs a simple simulation CSV.
 */
export function isOpenRocketCsv(text: string): boolean {
  const lines = text.split('\n', 20);
  return lines.some((l) => /^#.*Event\s+\w+\s+occurred/i.test(l));
}
