import type { SimRow, FlightEvent, OpenRocketData } from './types.js';

/**
 * OpenRocket column header → our internal field name.
 * Mirrors the Python COLUMN_MAP in openrocket_import.py.
 */
const COLUMN_MAP: Record<string, string> = {
  // Time
  'time': 'time_s',
  '# time': 'time_s',
  // Altitude
  'altitude': 'altitude_m',
  'height': 'altitude_m',
  // Velocity
  'vertical velocity': 'velocity_ms',
  'vertical speed': 'velocity_ms',
  'total velocity': 'total_velocity_ms',
  'total speed': 'total_velocity_ms',
  'lateral velocity': 'lateral_velocity_ms',
  // Acceleration
  'vertical acceleration': 'acceleration_ms2',
  'total acceleration': 'total_acceleration_ms2',
  // Aero
  'mach number': 'mach',
  'mach': 'mach',
  'drag force': 'drag_N',
  'drag coefficient': 'cd',
  'thrust': 'thrust_N',
  'normal force coefficient': 'cn',
  'stability margin calibers': 'stability_cal',
  'stability margin': 'stability_cal',
  'angle of attack': 'aoa_deg',
  // Mass
  'mass': 'mass_kg',
  'total mass': 'mass_kg',
  'propellant mass': 'prop_mass_kg',
  // Atmosphere
  'air pressure': 'pressure_pa',
  'atmospheric pressure': 'pressure_pa',
  'pressure': 'pressure_pa',
  'air temperature': 'temperature_c',
  'temperature': 'temperature_c',
  'wind speed': 'wind_speed_ms',
  // Position
  'lateral distance': 'lateral_distance_m',
  'lateral direction': 'lateral_direction_deg',
  'position east of launch': 'pos_east_m',
  'position north of launch': 'pos_north_m',
};

/** Normalize an OpenRocket column header to our field name */
function normalizeHeader(header: string): string | null {
  // Strip units: "Altitude (m)" → "altitude"
  const clean = header
    .replace(/\s*\(.*?\)\s*/g, '')
    .trim()
    .toLowerCase()
    .replace(/^[#\s]+/, '')
    .trim();
  return COLUMN_MAP[clean] ?? null;
}

/** Unit conversion functions: unit string → converter */
const UNIT_CONVERTERS: Record<string, (v: number) => number> = {
  // Length
  'ft': (v) => v * 0.3048,
  'in': (v) => v * 0.0254,
  'km': (v) => v * 1000,
  'mi': (v) => v * 1609.34,
  // Velocity
  'ft/s': (v) => v * 0.3048,
  'mph': (v) => v * 0.44704,
  'km/h': (v) => v * 0.27778,
  'kph': (v) => v * 0.27778,
  'kn': (v) => v * 0.51444,
  // Acceleration
  'ft/s²': (v) => v * 0.3048,
  'ft/s^2': (v) => v * 0.3048,
  // Force
  'lbf': (v) => v * 4.44822,
  'kgf': (v) => v * 9.80665,
  // Mass
  'lb': (v) => v * 0.453592,
  'oz': (v) => v * 0.0283495,
  // Pressure
  'psi': (v) => v * 6894.76,
  'atm': (v) => v * 101325,
  'bar': (v) => v * 100000,
  'mbar': (v) => v * 100,
  'hpa': (v) => v * 100,
  'mmhg': (v) => v * 133.322,
  // Temperature
  '°f': (v) => (v - 32) * 5 / 9,
  'f': (v) => (v - 32) * 5 / 9,
  'k': (v) => v - 273.15,
};

/** Detect unit from header like "Altitude (ft)" and return converter or null */
function getUnitConverter(header: string): ((v: number) => number) | null {
  const match = header.match(/\(([^)]+)\)/);
  if (!match) return null;
  const unit = match[1].trim().toLowerCase();
  return UNIT_CONVERTERS[unit] ?? null;
}

/** Detect separator: try comma, semicolon, tab */
function detectSeparator(headerLine: string): string {
  for (const sep of [',', ';', '\t']) {
    if (headerLine.split(sep).length > 1) return sep;
  }
  throw new Error(`Cannot detect separator in header: ${headerLine}`);
}

/** Map OpenRocket events to our flight states */
const EVENT_STATE_MAP: Record<string, string> = {
  LAUNCH: 'BOOST',
  IGNITION: 'BOOST',
  LIFTOFF: 'BOOST',
  BURNOUT: 'COAST',
  APOGEE: 'APOGEE',
  RECOVERY_DEVICE_DEPLOYMENT: 'DROGUE',
  EJECTION_CHARGE: 'DROGUE',
  GROUND_HIT: 'LANDED',
  SIMULATION_END: 'LANDED',
  TUMBLE: 'DROGUE',
};

/** Assign flight states to rows based on events */
function assignStates(rows: SimRow[], events: FlightEvent[]): SimRow[] {
  const sorted = [...events].sort((a, b) => a.time_s - b.time_s);
  const stateChanges: Array<[number, string]> = [];
  for (const ev of sorted) {
    const mapped = EVENT_STATE_MAP[ev.event];
    if (mapped) stateChanges.push([ev.time_s, mapped]);
  }

  for (const row of rows) {
    let state = 'PAD';
    for (const [changeT, changeState] of stateChanges) {
      if (row.time_s >= changeT) state = changeState;
    }
    row.state = state;
  }
  return rows;
}

/**
 * Parse an OpenRocket CSV export file.
 *
 * Handles:
 * - Comment lines starting with #
 * - Flight event annotations in comments
 * - Various column orderings and header naming
 * - Unit detection and auto-conversion to SI
 * - Separator auto-detection (comma, semicolon, tab)
 *
 * Port of tools/openrocket_import.py parse_openrocket_csv().
 */
export function parseOpenRocketCsv(text: string): OpenRocketData {
  const lines = text.split('\n');

  // Extract comments and find header line
  const comments: string[] = [];
  let headerLine: string | null = null;
  let dataStart = 0;

  for (let i = 0; i < lines.length; i++) {
    const stripped = lines[i].trim();
    if (!stripped) continue;
    if (stripped.startsWith('#')) {
      comments.push(stripped.replace(/^#+\s*/, ''));
      continue;
    }
    // First non-comment, non-empty line is the header
    if (headerLine === null) {
      headerLine = stripped;
      dataStart = i + 1;
      break;
    }
  }

  if (!headerLine) throw new Error('Could not find header row in CSV');

  // Parse events from comments
  // Format: "Event APOGEE occurred at t=12.345 seconds"
  const events: FlightEvent[] = [];
  for (const comment of comments) {
    const match = comment.match(/Event\s+(\w+)\s+occurred\s+at\s+t\s*=\s*([\d.]+)/i);
    if (match) {
      events.push({
        event: match[1].toUpperCase(),
        time_s: parseFloat(match[2]),
      });
    }
  }

  // Parse header
  const sep = detectSeparator(headerLine);
  const headers = headerLine.split(sep).map((h) => h.trim());

  // Map column indices to our field names
  const fieldMap: Map<number, { field: string; header: string }> = new Map();
  for (let i = 0; i < headers.length; i++) {
    const field = normalizeHeader(headers[i]);
    if (field) fieldMap.set(i, { field, header: headers[i] });
  }

  // Verify time column exists
  const hasTime = Array.from(fieldMap.values()).some((v) => v.field === 'time_s');
  if (!hasTime) {
    throw new Error(
      `No time column found. Headers: ${headers.join(', ')}\nMapped: ${JSON.stringify(Object.fromEntries(fieldMap))}`,
    );
  }

  // Parse data rows into column arrays
  const rawColumns: Map<string, (number | null)[]> = new Map();
  for (const { field } of fieldMap.values()) {
    if (!rawColumns.has(field)) rawColumns.set(field, []);
  }

  for (let i = dataStart; i < lines.length; i++) {
    const stripped = lines[i].trim();
    if (!stripped || stripped.startsWith('#')) continue;

    const values = stripped.split(sep);
    for (const [colIdx, { field }] of fieldMap) {
      const arr = rawColumns.get(field)!;
      if (colIdx < values.length) {
        const num = parseFloat(values[colIdx]);
        arr.push(isNaN(num) ? null : num);
      } else {
        arr.push(null);
      }
    }
  }

  // Convert units
  for (const [colIdx, { field, header }] of fieldMap) {
    const converter = getUnitConverter(header);
    if (converter) {
      const arr = rawColumns.get(field)!;
      for (let i = 0; i < arr.length; i++) {
        if (arr[i] !== null) arr[i] = converter(arr[i]!);
      }
    }
  }

  // Build row objects
  const timeCol = rawColumns.get('time_s')!;
  const nRows = timeCol.length;
  const rows: SimRow[] = [];

  for (let j = 0; j < nRows; j++) {
    const row: Record<string, number | string | undefined> = {};
    for (const [field, arr] of rawColumns) {
      row[field] = arr[j] ?? undefined;
    }
    if (typeof row.time_s !== 'number') continue;
    rows.push(row as SimRow);
  }

  // Assign states from events
  assignStates(rows, events);

  const columnsFound = [...new Set(Array.from(fieldMap.values()).map((v) => v.field))];

  return {
    rows,
    events,
    metadata: {
      source: '',
      columnsFound,
      columnsRaw: headers,
      nEvents: events.length,
      nRows: rows.length,
    },
  };
}
