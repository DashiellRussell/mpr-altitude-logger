import { describe, it, expect } from 'vitest';
import {
  pressureToAltitude,
  rawToVoltage,
  decodeFlags,
  flagsToString,
  sparkline,
  voltageBar,
} from './utils.js';

describe('pressureToAltitude', () => {
  it('returns 0 for equal pressure and ground', () => {
    expect(pressureToAltitude(101325, 101325)).toBeCloseTo(0, 1);
  });

  it('returns positive altitude for lower pressure', () => {
    const alt = pressureToAltitude(100000, 101325);
    expect(alt).toBeGreaterThan(0);
    expect(alt).toBeLessThan(200); // should be ~110m
  });

  it('returns 0 for invalid inputs', () => {
    expect(pressureToAltitude(0, 101325)).toBe(0);
    expect(pressureToAltitude(101325, 0)).toBe(0);
    expect(pressureToAltitude(-1, 101325)).toBe(0);
  });
});

describe('rawToVoltage', () => {
  it('converts 3V3 rail (no divider)', () => {
    // Full-scale ADC = 3.3V
    expect(rawToVoltage(65535, 1.0)).toBeCloseTo(3.3, 1);
  });

  it('converts 5V rail (2x divider)', () => {
    // Mid-scale ADC with 2x divider = 3.3V actual
    expect(rawToVoltage(32768, 2.0)).toBeCloseTo(3.3, 0);
  });

  it('converts 9V rail (3x divider)', () => {
    expect(rawToVoltage(65535, 3.0)).toBeCloseTo(9.9, 0);
  });
});

describe('decodeFlags', () => {
  it('decodes ARMED', () => {
    expect(decodeFlags(0x01)).toEqual(['ARMED']);
  });

  it('decodes multiple flags', () => {
    expect(decodeFlags(0x07)).toEqual(['ARMED', 'DROGUE_FIRED', 'MAIN_FIRED']);
  });

  it('returns empty for zero', () => {
    expect(decodeFlags(0x00)).toEqual([]);
  });

  it('decodes ERROR', () => {
    expect(decodeFlags(0x08)).toEqual(['ERROR']);
  });
});

describe('flagsToString', () => {
  it('returns SAFE for no flags', () => {
    expect(flagsToString(0)).toBe('SAFE');
  });

  it('joins multiple flags', () => {
    expect(flagsToString(0x03)).toBe('ARMED|DROGUE_FIRED');
  });
});

describe('sparkline', () => {
  it('generates sparkline of correct width', () => {
    const values = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];
    const result = sparkline(values, 10);
    expect(result.length).toBe(10);
    // First char should be lowest, last should be highest
    expect(result[0]).toBe(' ');
    expect(result[result.trimEnd().length - 1]).toBe('█');
  });

  it('handles empty input', () => {
    const result = sparkline([], 5);
    expect(result.length).toBe(5);
  });

  it('handles constant values', () => {
    const result = sparkline([5, 5, 5, 5], 4);
    // All same value — should use middle-ish char
    expect(result.length).toBe(4);
  });
});

describe('voltageBar', () => {
  it('returns green for nominal voltage', () => {
    const { color, status } = voltageBar(3.3, 3.3, 3.0, 3.6);
    expect(color).toBe('green');
    expect(status).toBe('OK');
  });

  it('returns red for out-of-range voltage', () => {
    const { color, status } = voltageBar(2.5, 3.3, 3.0, 3.6);
    expect(color).toBe('red');
    expect(status).toBe('WARN');
  });

  it('returns yellow for borderline voltage', () => {
    const { color, status } = voltageBar(3.05, 3.3, 3.0, 3.6);
    expect(color).toBe('yellow');
    expect(status).toBe('OK');
  });
});
