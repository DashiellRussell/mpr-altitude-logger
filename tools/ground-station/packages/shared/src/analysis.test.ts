import { describe, it, expect } from 'vitest';
import { analyzeFlight, summarizeSim, suggestCdAdjustment } from './analysis.js';
import { FlightState, type FlightFrame, type SimRow } from './types.js';

function makeFrame(overrides: Partial<FlightFrame> & { timestamp_ms: number }): FlightFrame {
  return {
    state: FlightState.PAD,
    pressure_pa: 101325,
    temperature_c: 20,
    alt_raw_m: 0,
    alt_filtered_m: 0,
    vel_filtered_ms: 0,
    flags: 0,
    state_name: 'PAD',
    flags_list: [],
    v_3v3_mv: 3300,
    v_5v_mv: 5000,
    v_9v_mv: 9000,
    ...overrides,
  };
}

describe('analyzeFlight', () => {
  it('returns zeroed stats for empty frames', () => {
    const stats = analyzeFlight([], 2);
    expect(stats.nFrames).toBe(0);
    expect(stats.maxAlt).toBe(0);
    expect(stats.duration).toBe(0);
  });

  it('computes correct stats for a simple flight', () => {
    const frames: FlightFrame[] = [
      makeFrame({ timestamp_ms: 0, state: FlightState.PAD, alt_filtered_m: 0, vel_filtered_ms: 0 }),
      makeFrame({ timestamp_ms: 1000, state: FlightState.BOOST, alt_filtered_m: 50, vel_filtered_ms: 80 }),
      makeFrame({ timestamp_ms: 2000, state: FlightState.COAST, alt_filtered_m: 200, vel_filtered_ms: 40 }),
      makeFrame({ timestamp_ms: 5000, state: FlightState.APOGEE, alt_filtered_m: 500, vel_filtered_ms: 0, flags: 0x03 }),
      makeFrame({ timestamp_ms: 10000, state: FlightState.DROGUE, alt_filtered_m: 300, vel_filtered_ms: -20 }),
      makeFrame({ timestamp_ms: 20000, state: FlightState.MAIN, alt_filtered_m: 100, vel_filtered_ms: -8, flags: 0x05 }),
      makeFrame({ timestamp_ms: 30000, state: FlightState.LANDED, alt_filtered_m: 0, vel_filtered_ms: -1 }),
    ];

    const stats = analyzeFlight(frames, 2);
    expect(stats.nFrames).toBe(7);
    expect(stats.maxAlt).toBe(500);
    expect(stats.maxAltTime).toBe(5); // 5000ms = 5s
    expect(stats.maxVel).toBe(80);
    expect(stats.duration).toBe(30); // 30000ms = 30s
    expect(stats.transitions).toHaveLength(6); // PAD→BOOST→COAST→APOGEE→DROGUE→MAIN→LANDED
    expect(stats.drogueFired).toBe(true);
    expect(stats.mainFired).toBe(true);
    expect(stats.wasArmed).toBe(true);
  });

  it('detects power rail ranges', () => {
    const frames: FlightFrame[] = [
      makeFrame({ timestamp_ms: 0, v_3v3_mv: 3300, v_5v_mv: 5000, v_9v_mv: 9000 }),
      makeFrame({ timestamp_ms: 1000, v_3v3_mv: 3200, v_5v_mv: 4800, v_9v_mv: 8500 }),
      makeFrame({ timestamp_ms: 2000, v_3v3_mv: 3350, v_5v_mv: 5100, v_9v_mv: 9200 }),
    ];

    const stats = analyzeFlight(frames, 2);
    expect(stats.v3v3Range).toEqual([3200, 3350]);
    expect(stats.v5vRange).toEqual([4800, 5100]);
    expect(stats.v9vRange).toEqual([8500, 9200]);
  });
});

describe('summarizeSim', () => {
  it('summarizes simulation data', () => {
    const rows: SimRow[] = [
      { time_s: 0, altitude_m: 0, velocity_ms: 0 },
      { time_s: 5, altitude_m: 500, velocity_ms: 80 },
      { time_s: 10, altitude_m: 300, velocity_ms: -20 },
    ];

    const summary = summarizeSim(rows);
    expect(summary.maxAlt).toBe(500);
    expect(summary.maxAltTime).toBe(5);
    expect(summary.maxVel).toBe(80);
    expect(summary.duration).toBe(10);
  });
});

describe('suggestCdAdjustment', () => {
  it('suggests increase when actual < predicted', () => {
    const result = suggestCdAdjustment(400, 500, 0.45);
    expect(result.direction).toBe('increase');
    expect(result.newCd).toBeGreaterThan(0.45);
  });

  it('suggests decrease when actual > predicted', () => {
    const result = suggestCdAdjustment(600, 500, 0.45);
    expect(result.direction).toBe('decrease');
    expect(result.newCd).toBeLessThan(0.45);
  });

  it('returns none when close enough', () => {
    const result = suggestCdAdjustment(500, 500, 0.45);
    expect(result.direction).toBe('none');
  });
});
