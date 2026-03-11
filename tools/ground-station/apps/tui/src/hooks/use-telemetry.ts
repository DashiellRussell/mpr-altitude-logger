import { useState, useEffect, useRef, useCallback } from 'react';
import { pressureToAltitude, rawToVoltage, RAIL_SPECS } from '@mpr/shared';
import type { PicoLink } from '../serial/pico-link.js';
import { INIT_CODE, CALIBRATE_CODE, POLL_CMD } from '../serial/commands.js';

interface TelemetryState {
  active: boolean;
  pressure: number;
  temp: number;
  alt: number;
  velocity: number;
  v3: number;
  v5: number;
  v9: number;
  altHistory: number[];
  velHistory: number[];
  samples: number;
  groundPa: number;
  voltagesOk: boolean;
  baroSane: boolean;
  recalibrate: () => void;
}

const POLL_INTERVAL_MS = 500; // 2 Hz
const ALT_HISTORY_LEN = 40;

/**
 * React hook that polls Pico sensors at 2Hz when enabled.
 * Handles init, calibration, and live telemetry state.
 */
export function useTelemetry(pico: PicoLink, enabled: boolean): TelemetryState {
  const [active, setActive] = useState(false);
  const [pressure, setPressure] = useState(0);
  const [temp, setTemp] = useState(0);
  const [alt, setAlt] = useState(0);
  const [velocity, setVelocity] = useState(0);
  const [v3, setV3] = useState(0);
  const [v5, setV5] = useState(0);
  const [v9, setV9] = useState(0);
  const [altHistory, setAltHistory] = useState<number[]>([]);
  const [velHistory, setVelHistory] = useState<number[]>([]);
  const [samples, setSamples] = useState(0);
  const [groundPa, setGroundPa] = useState(0);

  const prevAltRef = useRef<number | null>(null);
  const prevTimeRef = useRef<number | null>(null);
  const initedRef = useRef(false);
  const pollingRef = useRef(false);

  // Init sensors and calibrate
  const initSensors = useCallback(async () => {
    if (!pico.connected || initedRef.current) return;
    try {
      const { stderr } = await pico.execRaw(INIT_CODE, 8000);
      if (stderr) return;
      initedRef.current = true;

      // Calibrate ground pressure
      const calResult = await pico.execRaw(CALIBRATE_CODE, 10000);
      if (calResult.stdout && !calResult.stderr) {
        const gpa = parseFloat(calResult.stdout.trim());
        if (!isNaN(gpa) && gpa > 0) {
          setGroundPa(gpa);
          setAltHistory([]);
          setVelHistory([]);
          setSamples(0);
          prevAltRef.current = null;
          prevTimeRef.current = null;
          setActive(true);
        }
      }
    } catch {
      // Init failed, will retry
    }
  }, [pico]);

  // Recalibrate ground pressure
  const recalibrate = useCallback(async () => {
    if (!pico.connected || !initedRef.current) return;
    try {
      const { stdout, stderr } = await pico.execRaw(CALIBRATE_CODE, 10000);
      if (stdout && !stderr) {
        const gpa = parseFloat(stdout.trim());
        if (!isNaN(gpa) && gpa > 0) {
          setGroundPa(gpa);
          setAltHistory([]);
          setVelHistory([]);
          setSamples(0);
          prevAltRef.current = null;
          prevTimeRef.current = null;
        }
      }
    } catch {
      // Calibration failed
    }
  }, [pico]);

  // Poll loop
  useEffect(() => {
    if (!enabled || !pico.connected) {
      return;
    }

    // Init if needed
    if (!initedRef.current) {
      initSensors();
    }

    const interval = setInterval(async () => {
      if (!pico.connected || !initedRef.current || pollingRef.current) return;

      pollingRef.current = true;
      try {
        const { stdout, stderr } = await pico.execRaw(POLL_CMD, 3000);
        if (stderr || !stdout) {
          pollingRef.current = false;
          return;
        }

        const parts = stdout.trim().split(',');
        if (parts.length < 5) {
          pollingRef.current = false;
          return;
        }

        const p = parseFloat(parts[0]);
        const t = parseFloat(parts[1]);
        const v3raw = parseInt(parts[2], 10);
        const v5raw = parseInt(parts[3], 10);
        const v9raw = parseInt(parts[4], 10);

        if (isNaN(p) || isNaN(t)) {
          pollingRef.current = false;
          return;
        }

        setPressure(p);
        setTemp(t);

        const newV3 = rawToVoltage(v3raw, RAIL_SPECS['3V3'].divider);
        const newV5 = rawToVoltage(v5raw, RAIL_SPECS['5V'].divider);
        const newV9 = rawToVoltage(v9raw, RAIL_SPECS['9V'].divider);
        setV3(newV3);
        setV5(newV5);
        setV9(newV9);

        setGroundPa((currentGroundPa) => {
          if (currentGroundPa > 0) {
            const newAlt = pressureToAltitude(p, currentGroundPa);
            setAlt(newAlt);
            setAltHistory((prev) => {
              const next = [...prev, newAlt];
              if (next.length > ALT_HISTORY_LEN) next.shift();
              return next;
            });

            // Compute velocity
            const now = Date.now() / 1000;
            if (prevAltRef.current !== null && prevTimeRef.current !== null) {
              const dt = now - prevTimeRef.current;
              if (dt > 0) {
                const newVel = (newAlt - prevAltRef.current) / dt;
                setVelocity(newVel);
                setVelHistory((prev) => {
                  const next = [...prev, newVel];
                  if (next.length > ALT_HISTORY_LEN) next.shift();
                  return next;
                });
              }
            }
            prevAltRef.current = newAlt;
            prevTimeRef.current = now;
          }
          return currentGroundPa;
        });

        setSamples((s) => s + 1);
      } catch {
        // Poll failed, will retry
      }
      pollingRef.current = false;
    }, POLL_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [enabled, pico, initSensors]);

  // Cleanup on disable
  useEffect(() => {
    if (!enabled) {
      initedRef.current = false;
      setActive(false);
    }
  }, [enabled]);

  // Compute derived state
  const voltagesOk =
    (v3 === 0 || (v3 >= RAIL_SPECS['3V3'].min && v3 <= RAIL_SPECS['3V3'].max)) &&
    (v5 === 0 || (v5 >= RAIL_SPECS['5V'].min && v5 <= RAIL_SPECS['5V'].max)) &&
    (v9 === 0 || (v9 >= RAIL_SPECS['9V'].min && v9 <= RAIL_SPECS['9V'].max));

  const baroSane = pressure === 0 || (pressure > 80000 && pressure < 110000);

  return {
    active,
    pressure,
    temp,
    alt,
    velocity,
    v3,
    v5,
    v9,
    altHistory,
    velHistory,
    samples,
    groundPa,
    voltagesOk,
    baroSane,
    recalibrate,
  };
}
