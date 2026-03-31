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
  const n = frames.length;

  // Single-pass: compute maxima, transitions, deployment events, and flags
  // Avoids creating large intermediate arrays that can blow the stack on 100K+ frame logs
  let maxAlt = -Infinity, maxAltIdx = 0;
  let maxVel = -Infinity, maxVelIdx = 0;
  let maxAccel = 0, maxAccelTime = 0;
  let wasArmed = false, hadError = false;
  let drogueFired = false, drogueTime: number | null = null;
  let mainFired = false, mainTime: number | null = null;
  let landingVelSum = 0;
  const transitions: StateTransition[] = [];
  let prevVel = frames[0].vel_filtered_ms;
  let prevTime = 0;

  for (let i = 0; i < n; i++) {
    const f = frames[i];
    const t = (f.timestamp_ms - t0) / 1000;
    const alt = f.alt_filtered_m;
    const vel = f.vel_filtered_ms;

    if (alt > maxAlt) { maxAlt = alt; maxAltIdx = i; }
    if (vel > maxVel) { maxVel = vel; maxVelIdx = i; }

    // Acceleration from velocity derivative
    if (i > 0) {
      const dt = t - prevTime;
      if (dt > 0) {
        const accel = (vel - prevVel) / dt;
        if (accel > maxAccel) { maxAccel = accel; maxAccelTime = t; }
      }
    }

    // State transitions
    if (i > 0 && f.state !== frames[i - 1].state) {
      transitions.push({
        time: t,
        from_state: STATE_NAMES[frames[i - 1].state] ?? '?',
        to_state: STATE_NAMES[f.state] ?? '?',
      });
    }

    // Deployment events
    if (!drogueFired && (f.flags & FLAG_DROGUE_FIRED)) { drogueFired = true; drogueTime = t; }
    if (!mainFired && (f.flags & FLAG_MAIN_FIRED)) { mainFired = true; mainTime = t; }

    // Flags
    if (f.flags & FLAG_ARMED) wasArmed = true;
    if (f.flags & FLAG_ERROR) hadError = true;

    // Landing velocity: accumulate last 10 frames
    if (i >= n - Math.min(10, n)) { landingVelSum += vel; }

    prevVel = vel;
    prevTime = t;
  }

  const duration = (frames[n - 1].timestamp_ms - t0) / 1000;
  const sampleRate = duration > 0 ? n / duration : 0;
  const nLand = Math.min(10, n);
  const landingVel = landingVelSum / nLand;

  // Helper: frame index → time in seconds
  const timeAt = (i: number) => (frames[i].timestamp_ms - t0) / 1000;

  const stats: FlightStats = {
    maxAlt, maxAltTime: timeAt(maxAltIdx),
    maxVel, maxVelTime: timeAt(maxVelIdx),
    maxAccel, maxAccelTime,
    duration, sampleRate, nFrames: n,
    landingVel,
    transitions,
    drogueFired, drogueTime,
    mainFired, mainTime,
    wasArmed, hadError,
    version,
  };

  // v3 diagnostic stats — single-pass to avoid large intermediate arrays
  if (version >= 3 && frames[0].frame_us !== undefined) {
    let frameUsSum = 0, maxFrameUs = 0, maxFrameUsIdx = 0;
    let flushCount = 0, flushSum = 0, maxFlushUs = 0, maxFlushUsIdx = 0;
    let freeKbStart = frames[0].free_kb ?? 0, freeKbEnd = 0, freeKbMin = Infinity;
    let maxTemp = -Infinity, maxTempIdx = 0, tempSum = 0;
    const frameUsVals: number[] = new Array(n); // needed for percentile sort

    for (let i = 0; i < n; i++) {
      const f = frames[i];
      const fus = f.frame_us ?? 0;
      frameUsVals[i] = fus;
      frameUsSum += fus;
      if (fus > maxFrameUs) { maxFrameUs = fus; maxFrameUsIdx = i; }

      const flus = f.flush_us ?? 0;
      if (flus > 0) {
        flushCount++;
        flushSum += flus;
        if (flus > maxFlushUs) { maxFlushUs = flus; maxFlushUsIdx = i; }
      }

      const fkb = f.free_kb ?? 0;
      if (i === n - 1) freeKbEnd = fkb;
      if (fkb < freeKbMin) freeKbMin = fkb;

      const ct = (f.cpu_temp_c ?? 40) - 40;
      tempSum += ct;
      if (ct > maxTemp) { maxTemp = ct; maxTempIdx = i; }
    }

    // frame_us percentile (sort is fine on a typed array)
    frameUsVals.sort((a, b) => a - b);
    const p95Idx = Math.floor(n * 0.95);

    const lastFrame = frames[n - 1];
    const cleanShutdown = lastFrame.state === 6; // LANDED state

    stats.diag = {
      frameUs: {
        avg: Math.round(frameUsSum / n),
        p95: frameUsVals[p95Idx],
        max: maxFrameUs,
        maxTime: timeAt(maxFrameUsIdx),
      },
      flushUs: {
        avg: flushCount > 0 ? Math.round(flushSum / flushCount) : 0,
        max: maxFlushUs,
        maxTime: timeAt(maxFlushUsIdx),
        count: flushCount,
      },
      freeKb: {
        start: freeKbStart,
        end: freeKbEnd,
        min: freeKbMin === Infinity ? 0 : freeKbMin,
        trend: freeKbEnd - freeKbStart,
      },
      cpuTemp: {
        avg: Math.round(tempSum / n),
        max: maxTemp,
        maxTime: timeAt(maxTempIdx),
      },
      i2cErrors: lastFrame.i2c_errors ?? 0,
      overruns: lastFrame.overruns ?? 0,
      cleanShutdown,
    };
  }

  // Power rail ranges — single pass, no intermediate arrays
  if (version >= 2) {
    let v3lo = Infinity, v3hi = -Infinity;
    let v5lo = Infinity, v5hi = -Infinity;
    let v9lo = Infinity, v9hi = -Infinity;
    for (let i = 0; i < n; i++) {
      const f = frames[i];
      const v3 = f.v_3v3_mv ?? 0, v5 = f.v_5v_mv ?? 0, v9 = f.v_9v_mv ?? 0;
      if (v3 < v3lo) v3lo = v3; if (v3 > v3hi) v3hi = v3;
      if (v5 < v5lo) v5lo = v5; if (v5 > v5hi) v5hi = v5;
      if (v9 < v9lo) v9lo = v9; if (v9 > v9hi) v9hi = v9;
    }
    stats.v3v3Range = [v3lo, v3hi];
    stats.v5vRange = [v5lo, v5hi];
    stats.v9vRange = [v9lo, v9hi];
  } else {
    let blo = Infinity, bhi = -Infinity;
    for (let i = 0; i < n; i++) {
      const v = frames[i].v_batt_mv ?? 0;
      if (v < blo) blo = v; if (v > bhi) bhi = v;
    }
    stats.vBattRange = [blo, bhi];
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
    maxVel: velocities.reduce((a, b) => a > b ? a : b, velocities[0]),
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
