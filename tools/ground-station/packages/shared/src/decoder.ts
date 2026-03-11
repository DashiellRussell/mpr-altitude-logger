import { FlightState, type FlightFrame, type DecodedFlight } from './types.js';
import { FILE_MAGIC, FILE_HEADER_SIZE, FRAME_V1, FRAME_V2, STATE_NAMES } from './constants.js';
import { decodeFlags } from './utils.js';

/**
 * Read a field from a DataView at the given offset.
 * All values are little-endian (RP2040 native byte order).
 */
function readField(view: DataView, offset: number, type: string): number {
  switch (type) {
    case 'u8':
      return view.getUint8(offset);
    case 'u16':
      return view.getUint16(offset, true);
    case 'u32':
      return view.getUint32(offset, true);
    case 'f32':
      return view.getFloat32(offset, true);
    default:
      throw new Error(`Unknown field type: ${type}`);
  }
}

/**
 * Decode a single frame from the buffer at the given offset.
 * Returns the frame and the number of bytes consumed, or null if not enough data.
 */
function decodeFrame(
  view: DataView,
  offset: number,
  frameSpec: typeof FRAME_V1 | typeof FRAME_V2,
  version: number,
): FlightFrame | null {
  if (offset + frameSpec.size > view.byteLength) return null;

  const values: Record<string, number> = {};
  for (const field of frameSpec.fields) {
    values[field.name] = readField(view, offset + field.offset, field.type);
  }

  const state = values.state as FlightState;
  const flags = values.flags;

  const frame: FlightFrame = {
    timestamp_ms: values.timestamp_ms,
    state,
    pressure_pa: values.pressure_pa,
    temperature_c: values.temperature_c,
    alt_raw_m: values.alt_raw_m,
    alt_filtered_m: values.alt_filtered_m,
    vel_filtered_ms: values.vel_filtered_ms,
    flags,
    state_name: STATE_NAMES[state] ?? 'UNKNOWN',
    flags_list: decodeFlags(flags),
  };

  if (version >= 2) {
    frame.v_3v3_mv = values.v_3v3_mv;
    frame.v_5v_mv = values.v_5v_mv;
    frame.v_9v_mv = values.v_9v_mv;
  } else {
    frame.v_batt_mv = values.v_batt_mv;
  }

  return frame;
}

/**
 * Decode a binary flight log file.
 *
 * Handles:
 * - RKTLOG file header with version detection
 * - v1 (28-byte) and v2 (32-byte) frame formats
 * - Sync byte scanning (0xAA 0x55) for frame alignment / corruption recovery
 * - Missing file header (attempts raw decode)
 *
 * Matches the Python decode_log.py / postflight.py decoder behavior.
 */
export function decodeBinFile(buffer: Uint8Array): DecodedFlight {
  const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength);

  let version = 2;
  let offset = 0;

  // Check for file header
  const magic = String.fromCharCode(...buffer.slice(0, 6));
  if (magic === FILE_MAGIC) {
    version = view.getUint16(6, true);
    const _frameSize = view.getUint16(8, true);
    offset = FILE_HEADER_SIZE;
  }
  // If no header, try raw decode starting at offset 0

  const frameSpec = version >= 2 ? FRAME_V2 : FRAME_V1;
  const frames: FlightFrame[] = [];
  let skippedBytes = 0;

  while (offset + 2 + frameSpec.size <= buffer.byteLength) {
    // Look for sync header 0xAA 0x55
    if (buffer[offset] !== 0xaa || buffer[offset + 1] !== 0x55) {
      offset++;
      skippedBytes++;
      continue;
    }

    offset += 2; // skip sync bytes

    if (offset + frameSpec.size > buffer.byteLength) break;

    const frame = decodeFrame(view, offset, frameSpec, version);
    if (frame) {
      frames.push(frame);
    }
    offset += frameSpec.size;
  }

  return { frames, version, skippedBytes };
}

/**
 * Convert decoded frames to CSV string.
 */
export function framesToCsv(frames: FlightFrame[], version: number): string {
  if (!frames.length) return '';

  const baseFields = [
    'timestamp_ms',
    'state',
    'pressure_pa',
    'temperature_c',
    'alt_raw_m',
    'alt_filtered_m',
    'vel_filtered_ms',
  ];

  const voltageFields =
    version >= 2 ? ['v_3v3_mv', 'v_5v_mv', 'v_9v_mv'] : ['v_batt_mv'];

  const fields = [...baseFields, ...voltageFields, 'flags', 'state_name', 'flags_str'];

  const header = fields.join(',');
  const rows = frames.map((f) => {
    const vals = baseFields.map((k) => f[k as keyof FlightFrame]);
    const vVals = voltageFields.map((k) => f[k as keyof FlightFrame] ?? '');
    const flagsStr = f.flags_list.length ? f.flags_list.join('|') : 'SAFE';
    return [...vals, ...vVals, f.flags, f.state_name, flagsStr].join(',');
  });

  return [header, ...rows].join('\n');
}
