import { describe, it, expect } from 'vitest';
import { decodeBinFile, framesToCsv } from './decoder.js';
import { FlightState } from './types.js';

/** Build a minimal v2 .bin file for testing */
function buildTestBin(frames: Array<{
  timestamp_ms: number;
  state: number;
  pressure_pa: number;
  temperature_c: number;
  alt_raw_m: number;
  alt_filtered_m: number;
  vel_filtered_ms: number;
  v_3v3_mv: number;
  v_5v_mv: number;
  v_9v_mv: number;
  flags: number;
}>): Uint8Array {
  const FRAME_SIZE = 32;
  // Header: RKTLOG (6) + version u16 (2) + frame_size u16 (2) = 10 bytes
  const headerSize = 10;
  const totalSize = headerSize + frames.length * (2 + FRAME_SIZE); // 2 sync bytes per frame
  const buf = new ArrayBuffer(totalSize);
  const view = new DataView(buf);
  const u8 = new Uint8Array(buf);

  // Write header
  const magic = 'RKTLOG';
  for (let i = 0; i < 6; i++) u8[i] = magic.charCodeAt(i);
  view.setUint16(6, 2, true); // version 2
  view.setUint16(8, FRAME_SIZE, true);

  let offset = headerSize;
  for (const f of frames) {
    // Sync bytes
    u8[offset] = 0xaa;
    u8[offset + 1] = 0x55;
    offset += 2;

    // Frame data (little-endian)
    view.setUint32(offset + 0, f.timestamp_ms, true);
    view.setUint8(offset + 4, f.state);
    view.setFloat32(offset + 5, f.pressure_pa, true);
    view.setFloat32(offset + 9, f.temperature_c, true);
    view.setFloat32(offset + 13, f.alt_raw_m, true);
    view.setFloat32(offset + 17, f.alt_filtered_m, true);
    view.setFloat32(offset + 21, f.vel_filtered_ms, true);
    view.setUint16(offset + 25, f.v_3v3_mv, true);
    view.setUint16(offset + 27, f.v_5v_mv, true);
    view.setUint16(offset + 29, f.v_9v_mv, true);
    view.setUint8(offset + 31, f.flags);
    offset += FRAME_SIZE;
  }

  return u8;
}

describe('decodeBinFile', () => {
  it('decodes a valid v2 file with one frame', () => {
    const bin = buildTestBin([
      {
        timestamp_ms: 1000,
        state: FlightState.PAD,
        pressure_pa: 101325.0,
        temperature_c: 22.5,
        alt_raw_m: 0.0,
        alt_filtered_m: 0.0,
        vel_filtered_ms: 0.0,
        v_3v3_mv: 3300,
        v_5v_mv: 5000,
        v_9v_mv: 9000,
        flags: 0x01, // ARMED
      },
    ]);

    const result = decodeBinFile(bin);
    expect(result.version).toBe(2);
    expect(result.frames).toHaveLength(1);
    expect(result.skippedBytes).toBe(0);

    const f = result.frames[0];
    expect(f.timestamp_ms).toBe(1000);
    expect(f.state).toBe(FlightState.PAD);
    expect(f.state_name).toBe('PAD');
    expect(f.v_3v3_mv).toBe(3300);
    expect(f.v_5v_mv).toBe(5000);
    expect(f.v_9v_mv).toBe(9000);
    expect(f.flags_list).toEqual(['ARMED']);
    // Float precision: check within tolerance
    expect(f.pressure_pa).toBeCloseTo(101325.0, 0);
    expect(f.temperature_c).toBeCloseTo(22.5, 1);
  });

  it('decodes multiple frames with state transitions', () => {
    const bin = buildTestBin([
      {
        timestamp_ms: 0, state: FlightState.PAD,
        pressure_pa: 101325, temperature_c: 20, alt_raw_m: 0, alt_filtered_m: 0,
        vel_filtered_ms: 0, v_3v3_mv: 3300, v_5v_mv: 5000, v_9v_mv: 9000, flags: 0x01,
      },
      {
        timestamp_ms: 1000, state: FlightState.BOOST,
        pressure_pa: 100000, temperature_c: 20, alt_raw_m: 50, alt_filtered_m: 48,
        vel_filtered_ms: 80, v_3v3_mv: 3280, v_5v_mv: 4950, v_9v_mv: 8900, flags: 0x01,
      },
      {
        timestamp_ms: 5000, state: FlightState.APOGEE,
        pressure_pa: 95000, temperature_c: 18, alt_raw_m: 500, alt_filtered_m: 498,
        vel_filtered_ms: 0, v_3v3_mv: 3250, v_5v_mv: 4900, v_9v_mv: 8800, flags: 0x03,
      },
    ]);

    const result = decodeBinFile(bin);
    expect(result.frames).toHaveLength(3);
    expect(result.frames[0].state_name).toBe('PAD');
    expect(result.frames[1].state_name).toBe('BOOST');
    expect(result.frames[2].state_name).toBe('APOGEE');
    expect(result.frames[2].flags_list).toContain('ARMED');
    expect(result.frames[2].flags_list).toContain('DROGUE_FIRED');
  });

  it('handles missing file header (raw decode)', () => {
    // Build raw frames without RKTLOG header — just sync + frame data
    const FRAME_SIZE = 32;
    const buf = new ArrayBuffer(2 + FRAME_SIZE);
    const view = new DataView(buf);
    const u8 = new Uint8Array(buf);

    u8[0] = 0xaa;
    u8[1] = 0x55;
    view.setUint32(2, 500, true); // timestamp
    view.setUint8(6, FlightState.COAST);
    view.setFloat32(7, 99000, true);
    view.setFloat32(11, 19.5, true);
    view.setFloat32(15, 200, true);
    view.setFloat32(19, 198, true);
    view.setFloat32(23, 30, true);
    view.setUint16(27, 3300, true);
    view.setUint16(29, 5000, true);
    view.setUint16(31, 9000, true);
    view.setUint8(33, 0x01);

    const result = decodeBinFile(u8);
    // Should still decode (assumes v2 when no header)
    expect(result.frames).toHaveLength(1);
    expect(result.frames[0].state_name).toBe('COAST');
  });

  it('skips corrupted bytes and resyncs', () => {
    // Build valid frame, then garbage, then another valid frame
    const validFrame = buildTestBin([
      {
        timestamp_ms: 100, state: FlightState.PAD,
        pressure_pa: 101325, temperature_c: 20, alt_raw_m: 0, alt_filtered_m: 0,
        vel_filtered_ms: 0, v_3v3_mv: 3300, v_5v_mv: 5000, v_9v_mv: 9000, flags: 0,
      },
    ]);

    // Inject 5 garbage bytes between the header and the frame
    const result = new Uint8Array(validFrame.length + 5);
    // Copy header
    result.set(validFrame.slice(0, 10), 0);
    // Garbage
    result.set(new Uint8Array([0xff, 0xfe, 0xfd, 0xfc, 0xfb]), 10);
    // Copy frame (sync + data)
    result.set(validFrame.slice(10), 15);

    const decoded = decodeBinFile(result);
    expect(decoded.frames).toHaveLength(1);
    expect(decoded.skippedBytes).toBe(5);
  });

  it('returns empty for empty buffer', () => {
    const result = decodeBinFile(new Uint8Array(0));
    expect(result.frames).toHaveLength(0);
    expect(result.version).toBe(2);
  });
});

describe('framesToCsv', () => {
  it('generates CSV with correct headers for v2', () => {
    const bin = buildTestBin([
      {
        timestamp_ms: 0, state: FlightState.PAD,
        pressure_pa: 101325, temperature_c: 20, alt_raw_m: 0, alt_filtered_m: 0,
        vel_filtered_ms: 0, v_3v3_mv: 3300, v_5v_mv: 5000, v_9v_mv: 9000, flags: 0,
      },
    ]);
    const { frames, version } = decodeBinFile(bin);
    const csv = framesToCsv(frames, version);
    const lines = csv.split('\n');

    expect(lines[0]).toContain('timestamp_ms');
    expect(lines[0]).toContain('v_3v3_mv');
    expect(lines[0]).toContain('state_name');
    expect(lines[0]).toContain('flags_str');
    expect(lines).toHaveLength(2); // header + 1 row
    expect(lines[1]).toContain('PAD');
    expect(lines[1]).toContain('SAFE');
  });
});
