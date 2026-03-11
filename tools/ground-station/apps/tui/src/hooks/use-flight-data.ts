import { useState, useEffect } from 'react';
import { readFileSync } from 'fs';
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
 * React hook that loads and decodes a .bin flight log file,
 * optionally with simulation CSV for comparison.
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

      // Load simulation data if provided
      if (simFile) {
        try {
          const simText = readFileSync(simFile, 'utf-8');
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
          setError(`Warning: could not load sim file: ${e instanceof Error ? e.message : String(e)}`);
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
