import { useState, useEffect } from 'react';
import { readFileSync, readdirSync, existsSync } from 'fs';
import { join, resolve } from 'path';
import {
  decodeBinFile,
  analyzeFlight,
  parseSimCsv,
  isOpenRocketCsv,
  parseOpenRocketCsv,
  summarizeSim,
} from '@mpr/shared';
import type { FlightFrame, FlightStats, SimSummary } from '@mpr/shared';

interface FlightDataResult {
  loading: boolean;
  error: string | null;
  frames: FlightFrame[];
  stats: FlightStats | null;
  sim: SimSummary | null;
  version: number;
  skippedBytes: number;
}

/**
 * Auto-discover the latest sim CSV in the sims/ directory.
 * Walks up from cwd looking for a sims/ folder with *_sim.csv or *.csv files.
 */
function findSimFile(): string | null {
  // Check common locations relative to the ground-station root and repo root
  const candidates = [
    resolve(process.cwd(), '../../sims'),       // from apps/tui/
    resolve(process.cwd(), '../../../sims'),     // from apps/tui/src/
    resolve(process.cwd(), 'sims'),              // from repo root
    resolve(process.cwd(), '../sims'),           // one level up
  ];

  for (const dir of candidates) {
    if (!existsSync(dir)) continue;
    try {
      const files = readdirSync(dir)
        .filter(f => f.endsWith('.csv'))
        .sort(); // alphabetical, *_sim.csv files will be found
      // Prefer *_sim.csv files (extracted from .ork)
      const simCsv = files.find(f => f.includes('_sim'));
      if (simCsv) return join(dir, simCsv);
      // Fall back to any CSV
      if (files.length > 0) return join(dir, files[files.length - 1]);
    } catch {
      continue;
    }
  }
  return null;
}

/**
 * React hook that loads and decodes a .bin flight log file,
 * optionally with simulation CSV for comparison.
 * If no simFile is provided, auto-discovers from sims/ directory.
 */
export function useFlightData(binFile?: string, simFile?: string): FlightDataResult {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [frames, setFrames] = useState<FlightFrame[]>([]);
  const [stats, setStats] = useState<FlightStats | null>(null);
  const [sim, setSim] = useState<SimSummary | null>(null);
  const [version, setVersion] = useState(2);
  const [skippedBytes, setSkippedBytes] = useState(0);

  useEffect(() => {
    if (!binFile) {
      setLoading(false);
      setError('No flight log file specified');
      return;
    }

    try {
      // Read and decode binary log
      const buffer = readFileSync(binFile);
      const decoded = decodeBinFile(new Uint8Array(buffer));

      if (decoded.frames.length === 0) {
        setError('No valid frames found in log file');
        setLoading(false);
        return;
      }

      setFrames(decoded.frames);
      setVersion(decoded.version);
      setSkippedBytes(decoded.skippedBytes);

      // Analyze flight
      const flightStats = analyzeFlight(decoded.frames, decoded.version);
      setStats(flightStats);

      // Load simulation data — explicit file or auto-discover from sims/
      const resolvedSim = simFile || findSimFile();
      if (resolvedSim) {
        try {
          const simText = readFileSync(resolvedSim, 'utf-8');
          let simRows;
          if (isOpenRocketCsv(simText)) {
            const orData = parseOpenRocketCsv(simText);
            simRows = orData.rows;
          } else {
            simRows = parseSimCsv(simText);
          }
          const simSummary = summarizeSim(simRows);
          setSim(simSummary);
        } catch (e) {
          // Sim loading failed, continue without it
          setError(`Warning: could not load sim file (${resolvedSim}): ${e instanceof Error ? e.message : String(e)}`);
        }
      }

      setLoading(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setLoading(false);
    }
  }, [binFile, simFile]);

  return { loading, error, frames, stats, sim, version, skippedBytes };
}
