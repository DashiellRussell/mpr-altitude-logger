/** Flight state enum matching MicroPython state_machine.py */
export enum FlightState {
  PAD = 0,
  BOOST = 1,
  COAST = 2,
  APOGEE = 3,
  DROGUE = 4,
  MAIN = 5,
  LANDED = 6,
}

/** Single decoded telemetry frame from the binary log */
export interface FlightFrame {
  timestamp_ms: number;
  state: FlightState;
  pressure_pa: number;
  temperature_c: number;
  alt_raw_m: number;
  alt_filtered_m: number;
  vel_filtered_ms: number;
  /** v1 only — single battery rail */
  v_batt_mv?: number;
  /** v2 — individual rails */
  v_3v3_mv?: number;
  v_5v_mv?: number;
  v_9v_mv?: number;
  flags: number;
  /** Derived */
  state_name: string;
  flags_list: string[];
}

/** Result of decoding a .bin flight log */
export interface DecodedFlight {
  frames: FlightFrame[];
  version: number;
  skippedBytes: number;
}

/** Row from simulation CSV (simulate.py or openrocket_import.py output) */
export interface SimRow {
  time_s: number;
  altitude_m: number;
  velocity_ms: number;
  acceleration_ms2?: number;
  mach?: number;
  thrust_N?: number;
  drag_N?: number;
  mass_kg?: number;
  pressure_pa?: number;
  air_density?: number;
  state?: string;
  [key: string]: number | string | undefined;
}

/** State transition event */
export interface StateTransition {
  time: number;
  from_state: string;
  to_state: string;
}

/** Computed flight statistics */
export interface FlightStats {
  maxAlt: number;
  maxAltTime: number;
  maxVel: number;
  maxVelTime: number;
  maxAccel: number;
  maxAccelTime: number;
  duration: number;
  sampleRate: number;
  nFrames: number;
  landingVel: number;
  transitions: StateTransition[];
  drogueFired: boolean;
  drogueTime: number | null;
  mainFired: boolean;
  mainTime: number | null;
  wasArmed: boolean;
  hadError: boolean;
  version: number;
  /** Power rail ranges (v2 only) */
  v3v3Range?: [number, number];
  v5vRange?: [number, number];
  v9vRange?: [number, number];
  /** Battery range (v1 only) */
  vBattRange?: [number, number];
}

/** OpenRocket flight event */
export interface FlightEvent {
  event: string;
  time_s: number;
}

/** OpenRocket parsed result */
export interface OpenRocketData {
  rows: SimRow[];
  events: FlightEvent[];
  metadata: {
    source: string;
    columnsFound: string[];
    columnsRaw: string[];
    nEvents: number;
    nRows: number;
  };
}

/** Simulation data summary (for comparison) */
export interface SimSummary {
  maxAlt: number;
  maxAltTime: number;
  maxVel: number;
  duration: number;
  times: number[];
  altitudes: number[];
  velocities: number[];
}

/** Rail specification: nominal, min, max, divider ratio */
export interface RailSpec {
  nominal: number;
  min: number;
  max: number;
  divider: number;
}
