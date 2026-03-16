import { FlightState, type RailSpec } from './types.js';

/** Human-readable state names indexed by FlightState enum */
export const STATE_NAMES: Record<number, string> = {
  [FlightState.PAD]: 'PAD',
  [FlightState.BOOST]: 'BOOST',
  [FlightState.COAST]: 'COAST',
  [FlightState.APOGEE]: 'APOGEE',
  [FlightState.DROGUE]: 'DROGUE',
  [FlightState.MAIN]: 'MAIN',
  [FlightState.LANDED]: 'LANDED',
};

/** Colors for state display (terminal + web) */
export const STATE_COLORS: Record<string, string> = {
  PAD: 'white',
  BOOST: 'red',
  COAST: 'yellow',
  APOGEE: 'green',
  DROGUE: 'cyan',
  MAIN: 'blue',
  LANDED: 'magenta',
};

/** Flag bitmask constants — ARMED/DROGUE/MAIN are legacy (always 0 in new logs, kept for old log compat) */
export const FLAG_ARMED = 0x01;
export const FLAG_DROGUE_FIRED = 0x02;
export const FLAG_MAIN_FIRED = 0x04;
export const FLAG_ERROR = 0x08;

/** Voltage rail specifications: nominal, min, max, divider */
export const RAIL_SPECS: Record<string, RailSpec> = {
  '3V3': { nominal: 3.3, min: 3.0, max: 3.6, divider: 1.0 },
  '5V': { nominal: 5.0, min: 4.5, max: 5.5, divider: 1.735 },
  '9V': { nominal: 9.0, min: 8.0, max: 10.0, divider: 3.0 },
};

/** ADC reference voltage and resolution */
export const ADC_VREF = 3.3;
export const ADC_RESOLUTION = 65535;

/** Binary log file header */
export const FILE_MAGIC = 'RKTLOG';
export const FILE_HEADER_SIZE = 10; // 6 magic + 2 version + 2 frame_size

/** Frame sync marker */
export const FRAME_SYNC = new Uint8Array([0xaa, 0x55]);

/**
 * Frame layout definitions.
 * Offsets are relative to start of frame data (after sync bytes).
 */
export const FRAME_V3 = {
  size: 40,
  fields: [
    { name: 'timestamp_ms', offset: 0, type: 'u32' },
    { name: 'state', offset: 4, type: 'u8' },
    { name: 'pressure_pa', offset: 5, type: 'f32' },
    { name: 'temperature_c', offset: 9, type: 'f32' },
    { name: 'alt_raw_m', offset: 13, type: 'f32' },
    { name: 'alt_filtered_m', offset: 17, type: 'f32' },
    { name: 'vel_filtered_ms', offset: 21, type: 'f32' },
    { name: 'v_3v3_mv', offset: 25, type: 'u16' },
    { name: 'v_5v_mv', offset: 27, type: 'u16' },
    { name: 'v_9v_mv', offset: 29, type: 'u16' },
    { name: 'flags', offset: 31, type: 'u8' },
    { name: 'frame_us', offset: 32, type: 'u16' },
    { name: 'flush_us', offset: 34, type: 'u16' },
    { name: 'free_kb', offset: 36, type: 'u8' },
    { name: 'cpu_temp_c', offset: 37, type: 'u8' },
    { name: 'i2c_errors', offset: 38, type: 'u8' },
    { name: 'overruns', offset: 39, type: 'u8' },
  ],
} as const;

export const FRAME_V2 = {
  size: 32,
  fields: [
    { name: 'timestamp_ms', offset: 0, type: 'u32' },
    { name: 'state', offset: 4, type: 'u8' },
    { name: 'pressure_pa', offset: 5, type: 'f32' },
    { name: 'temperature_c', offset: 9, type: 'f32' },
    { name: 'alt_raw_m', offset: 13, type: 'f32' },
    { name: 'alt_filtered_m', offset: 17, type: 'f32' },
    { name: 'vel_filtered_ms', offset: 21, type: 'f32' },
    { name: 'v_3v3_mv', offset: 25, type: 'u16' },
    { name: 'v_5v_mv', offset: 27, type: 'u16' },
    { name: 'v_9v_mv', offset: 29, type: 'u16' },
    { name: 'flags', offset: 31, type: 'u8' },
  ],
} as const;

export const FRAME_V1 = {
  size: 28,
  fields: [
    { name: 'timestamp_ms', offset: 0, type: 'u32' },
    { name: 'state', offset: 4, type: 'u8' },
    { name: 'pressure_pa', offset: 5, type: 'f32' },
    { name: 'temperature_c', offset: 9, type: 'f32' },
    { name: 'alt_raw_m', offset: 13, type: 'f32' },
    { name: 'alt_filtered_m', offset: 17, type: 'f32' },
    { name: 'vel_filtered_ms', offset: 21, type: 'f32' },
    { name: 'v_batt_mv', offset: 25, type: 'u16' },
    { name: 'flags', offset: 27, type: 'u8' },
  ],
} as const;

/** Sparkline character set */
export const SPARKLINE_CHARS = ' ▁▂▃▄▅▆▇█';

/** Spinner frames for terminal animations */
export const SPINNER_FRAMES = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏';

/** Kalman filter defaults (from config.py) */
export const KALMAN_DEFAULTS = {
  Q_ALT: 0.1,
  Q_VEL: 0.5,
  R_ALT: 1.0,
};

/** State machine thresholds (from config.py) */
export const STATE_THRESHOLDS = {
  LAUNCH_ALT_THRESHOLD: 15.0,
  LAUNCH_VEL_THRESHOLD: 10.0,
  LAUNCH_DETECT_WINDOW: 0.5,
  BOOST_RECOVERY_ALT: 10.0,
  BOOST_RECOVERY_WINDOW: 2.0,
  COAST_VEL_THRESHOLD: 5.0,
  COAST_TIMEOUT: 30.0,
  APOGEE_VEL_THRESHOLD: 2.0,
  APOGEE_CONFIRM_COUNT: 5,
  LANDED_VEL_THRESHOLD: 0.5,
  LANDED_CONFIRM_SECONDS: 5.0,
  MAIN_CHUTE_FRACTION: 0.25,
};
