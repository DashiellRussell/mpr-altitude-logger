import {
  FLAG_ARMED,
  FLAG_DROGUE_FIRED,
  FLAG_MAIN_FIRED,
  FLAG_ERROR,
  SPARKLINE_CHARS,
  ADC_VREF,
  ADC_RESOLUTION,
} from './constants.js';

/**
 * Hypsometric formula: barometric pressure → altitude AGL.
 * Matches the MicroPython implementation in sensors/barometer.py.
 */
export function pressureToAltitude(pressurePa: number, groundPa: number): number {
  if (pressurePa <= 0 || groundPa <= 0) return 0;
  return 44330.0 * (1.0 - Math.pow(pressurePa / groundPa, 0.1903));
}

/**
 * Convert ADC raw u16 reading to actual voltage through divider.
 * RP2040 ADC returns 16-bit scaled values via MicroPython read_u16().
 */
export function rawToVoltage(raw: number, divider: number): number {
  return (raw / ADC_RESOLUTION) * ADC_VREF * divider;
}

/** Decode flags bitmask to array of flag name strings */
export function decodeFlags(flags: number): string[] {
  const parts: string[] = [];
  if (flags & FLAG_ARMED) parts.push('ARMED');
  if (flags & FLAG_DROGUE_FIRED) parts.push('DROGUE_FIRED');
  if (flags & FLAG_MAIN_FIRED) parts.push('MAIN_FIRED');
  if (flags & FLAG_ERROR) parts.push('ERROR');
  return parts;
}

/** Flags byte to display string */
export function flagsToString(flags: number): string {
  const parts = decodeFlags(flags);
  return parts.length > 0 ? parts.join('|') : 'SAFE';
}

/**
 * Generate a unicode sparkline from a numeric array.
 * Downsamples to `width` characters if needed.
 */
export function sparkline(values: number[], width: number = 50): string {
  if (!values.length) return SPARKLINE_CHARS[0].repeat(width);

  // Downsample
  const step = Math.max(1, Math.floor(values.length / width));
  const sampled: number[] = [];
  for (let i = 0; i < values.length && sampled.length < width; i += step) {
    sampled.push(values[i]);
  }

  const min = Math.min(...sampled);
  const max = Math.max(...sampled);
  const range = max - min || 1;
  const n = SPARKLINE_CHARS.length - 1;

  return sampled
    .map((v) => {
      const idx = Math.max(0, Math.min(n, Math.round(((v - min) / range) * n)));
      return SPARKLINE_CHARS[idx];
    })
    .join('')
    .padEnd(width);
}

/**
 * Generate a voltage bar string for terminal display.
 * Returns [bar, color, status] where color is a terminal color name.
 */
export function voltageBar(
  actual: number,
  nominal: number,
  minOk: number,
  maxOk: number,
  width: number = 30,
): { bar: string; color: string; status: string } {
  const ratio = nominal > 0 ? actual / nominal : 0;
  const filled = Math.max(0, Math.min(width, Math.round(ratio * width)));
  const bar = '█'.repeat(filled) + '░'.repeat(width - filled);

  let color: string;
  let status: string;
  if (actual < minOk || actual > maxOk) {
    color = 'red';
    status = 'WARN';
  } else if (actual < minOk * 1.05 || actual > maxOk * 0.95) {
    color = 'yellow';
    status = 'OK';
  } else {
    color = 'green';
    status = 'OK';
  }

  return { bar, color, status };
}

/** Format milliseconds as "T+X.XXs" */
export function formatTime(ms: number): string {
  return `T+${(ms / 1000).toFixed(2)}s`;
}

/** Format voltage with unit */
export function formatVoltage(mv: number): string {
  return `${(mv / 1000).toFixed(2)}V`;
}

/** Clamp a number to [min, max] */
export function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}
