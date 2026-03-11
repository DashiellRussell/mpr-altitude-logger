import type { FlightFrame, FlightStats, StateTransition, SimRow, SimSummary } from './types.js';
import { FLAG_ARMED, FLAG_DROGUE_FIRED, FLAG_MAIN_FIRED, FLAG_ERROR, STATE_NAMES } from './constants.js';

/**
 * Analyze decoded flight frames and compute summary statistics.
 *
 * Port of FlightData class from tools/postflight.py.
 */
export function analyzeFlight(frames: FlightFrame[], version: number): FlightStats {
  if (!frames.length) {
    return {
      maxAlt: 0, maxAltTime: 0,
      maxVel: 0, maxVelTime: 0,
      maxAccel: 0, maxAccelTime: 0,
      duration: 0, sampleRate: 0, nFrames: 0,
      landingVel: 0,
      transitions: [],
      drogueFired: false, drogueTime: null,
      mainFired: false, mainTime: null,
      wasArmed: false, hadError: false,
      version,
    };
  }

  const t0 = frames[0].timestamp_ms;
  const times = frames.map((f) => (f.timestamp_ms - t0) / 1000);
  const altitudes = frames.map((f) => f.alt_filtered_m);
  const velocities = frames.map((f) => f.vel_filtered_ms);

  // Max altitude
  let maxAlt = -Infinity;
  let maxAltIdx = 0;
  for (let i = 0; i < altitudes.length; i++) {
    if (altitudes[i] > maxAlt) {
      maxAlt = altitudes[i];
      maxAltIdx = i;
    }
  }

  // Max velocity
  let maxVel = -Infinity;
  let maxVelIdx = 0;
  for (let i = 0; i < velocities.length; i++) {
    if (velocities[i] > maxVel) {
      maxVel = velocities[i];
      maxVelIdx = i;
    }
  }

  // Max acceleration (estimated from velocity derivative)
  let maxAccel = 0;
  let maxAccelTime = 0;
  for (let i = 1; i < velocities.length; i++) {
    const dt = times[i] - times[i - 1];
    if (dt > 0) {
      const accel = (velocities[i] - velocities[i - 1]) / dt;
      if (accel > maxAccel) {
        maxAccel = accel;
        maxAccelTime = times[i];
      }
    }
  }

  const duration = times[times.length - 1];
  const sampleRate = duration > 0 ? frames.length / duration : 0;

  // Landing velocity (average of last 10 frames)
  const nLand = Math.min(10, velocities.length);
  let landingVelSum = 0;
  for (let i = velocities.length - nLand; i < velocities.length; i++) {
    landingVelSum += velocities[i];
  }
  const landingVel = landingVelSum / nLand;

  // State transitions
  const transitions: StateTransition[] = [];
  for (let i = 1; i < frames.length; i++) {
    if (frames[i].state !== frames[i - 1].state) {
      transitions.push({
        time: times[i],
        from_state: STATE_NAMES[frames[i - 1].state] ?? '?',
        to_state: STATE_NAMES[frames[i].state] ?? '?',
      });
    }
  }

  // Deployment events
  let drogueFired = false;
  let drogueTime: number | null = null;
  let mainFired = false;
  let mainTime: number | null = null;

  for (let i = 0; i < frames.length; i++) {
    if (!drogueFired && (frames[i].flags & FLAG_DROGUE_FIRED)) {
      drogueFired = true;
      drogueTime = times[i];
    }
    if (!mainFired && (frames[i].flags & FLAG_MAIN_FIRED)) {
      mainFired = true;
      mainTime = times[i];
    }
  }

  // ARM and error status
  const wasArmed = frames.some((f) => f.flags & FLAG_ARMED);
  const hadError = frames.some((f) => f.flags & FLAG_ERROR);

  // Power rail ranges
  const stats: FlightStats = {
    maxAlt, maxAltTime: times[maxAltIdx],
    maxVel, maxVelTime: times[maxVelIdx],
    maxAccel, maxAccelTime,
    duration, sampleRate, nFrames: frames.length,
    landingVel,
    transitions,
    drogueFired, drogueTime,
    mainFired, mainTime,
    wasArmed, hadError,
    version,
  };

  if (version >= 2) {
    const v3vals = frames.map((f) => f.v_3v3_mv ?? 0);
    const v5vals = frames.map((f) => f.v_5v_mv ?? 0);
    const v9vals = frames.map((f) => f.v_9v_mv ?? 0);
    stats.v3v3Range = [Math.min(...v3vals), Math.max(...v3vals)];
    stats.v5vRange = [Math.min(...v5vals), Math.max(...v5vals)];
    stats.v9vRange = [Math.min(...v9vals), Math.max(...v9vals)];
  } else {
    const vBatt = frames.map((f) => f.v_batt_mv ?? 0);
    stats.vBattRange = [Math.min(...vBatt), Math.max(...vBatt)];
  }

  return stats;
}

/**
 * Summarize simulation data for comparison.
 */
export function summarizeSim(rows: SimRow[]): SimSummary {
  if (!rows.length) {
    return { maxAlt: 0, maxAltTime: 0, maxVel: 0, duration: 0, times: [], altitudes: [], velocities: [] };
  }

  const times = rows.map((r) => r.time_s);
  const altitudes = rows.map((r) => r.altitude_m ?? 0);
  const velocities = rows.map((r) => r.velocity_ms ?? 0);

  let maxAlt = -Infinity;
  let maxAltIdx = 0;
  for (let i = 0; i < altitudes.length; i++) {
    if (altitudes[i] > maxAlt) {
      maxAlt = altitudes[i];
      maxAltIdx = i;
    }
  }

  return {
    maxAlt,
    maxAltTime: times[maxAltIdx],
    maxVel: Math.max(...velocities),
    duration: times[times.length - 1],
    times,
    altitudes,
    velocities,
  };
}

/**
 * Suggest a Cd adjustment based on actual vs predicted apogee.
 * If actual apogee < predicted, Cd is too low (need to increase).
 */
export function suggestCdAdjustment(
  actualApogee: number,
  predictedApogee: number,
  currentCd: number = 0.45,
): { newCd: number; direction: 'increase' | 'decrease' | 'none' } {
  if (predictedApogee <= 0 || actualApogee <= 0) {
    return { newCd: currentCd, direction: 'none' };
  }

  const newCd = currentCd * Math.sqrt(predictedApogee / actualApogee);
  const diff = Math.abs(newCd - currentCd);

  if (diff < 0.005) return { newCd: currentCd, direction: 'none' };

  return {
    newCd,
    direction: newCd > currentCd ? 'increase' : 'decrease',
  };
}
