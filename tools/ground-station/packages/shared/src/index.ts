// Types
export type {
  FlightFrame,
  DecodedFlight,
  SimRow,
  FlightStats,
  StateTransition,
  FlightEvent,
  OpenRocketData,
  SimSummary,
  RailSpec,
} from './types.js';
export { FlightState } from './types.js';

// Constants
export {
  STATE_NAMES,
  STATE_COLORS,
  FLAG_ARMED,
  FLAG_DROGUE_FIRED,
  FLAG_MAIN_FIRED,
  FLAG_ERROR,
  RAIL_SPECS,
  ADC_VREF,
  ADC_RESOLUTION,
  FILE_MAGIC,
  FILE_HEADER_SIZE,
  FRAME_SYNC,
  FRAME_V1,
  FRAME_V2,
  SPARKLINE_CHARS,
  SPINNER_FRAMES,
  KALMAN_DEFAULTS,
  STATE_THRESHOLDS,
} from './constants.js';

// Decoder
export { decodeBinFile, framesToCsv } from './decoder.js';

// CSV Parsers
export { parseSimCsv, isOpenRocketCsv } from './sim-parser.js';
export { parseOpenRocketCsv } from './openrocket.js';

// Analysis
export { analyzeFlight, summarizeSim, suggestCdAdjustment } from './analysis.js';

// Utils
export {
  pressureToAltitude,
  rawToVoltage,
  decodeFlags,
  flagsToString,
  sparkline,
  voltageBar,
  formatTime,
  formatVoltage,
  clamp,
} from './utils.js';
