import { useState, useCallback } from 'react';
import type { FlightFrame, FlightStats, SimSummary } from '@mpr/shared';
import {
  decodeBinFile,
  analyzeFlight,
  parseSimCsv,
  isOpenRocketCsv,
  parseOpenRocketCsv,
  summarizeSim,
} from '@mpr/shared';

interface FlightDataState {
  frames: FlightFrame[];
  stats: FlightStats | null;
  simSummary: SimSummary | null;
  version: number;
  fileName: string;
  loading: boolean;
  error: string | null;
}

const INITIAL_STATE: FlightDataState = {
  frames: [],
  stats: null,
  simSummary: null,
  version: 1,
  fileName: '',
  loading: false,
  error: null,
};

export function useFlightData() {
  const [state, setState] = useState<FlightDataState>(INITIAL_STATE);

  const loadBinFile = useCallback((buffer: ArrayBuffer, name: string) => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const decoded = decodeBinFile(new Uint8Array(buffer));
      if (decoded.frames.length === 0) {
        setState((prev) => ({
          ...prev,
          loading: false,
          error: 'No valid frames found in file. Check format.',
        }));
        return;
      }
      const stats = analyzeFlight(decoded.frames, decoded.version);
      setState((prev) => ({
        ...prev,
        frames: decoded.frames,
        stats,
        version: decoded.version,
        fileName: name,
        loading: false,
      }));
    } catch (err) {
      setState((prev) => ({
        ...prev,
        loading: false,
        error: `Failed to decode .bin file: ${err instanceof Error ? err.message : String(err)}`,
      }));
    }
  }, []);

  const loadSimFile = useCallback((text: string, name: string) => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const isOR = isOpenRocketCsv(text);
      const rows = isOR ? parseOpenRocketCsv(text).rows : parseSimCsv(text);
      if (rows.length === 0) {
        setState((prev) => ({
          ...prev,
          loading: false,
          error: 'No valid rows found in CSV. Check format.',
        }));
        return;
      }
      const summary = summarizeSim(rows);
      setState((prev) => ({
        ...prev,
        simSummary: summary,
        loading: false,
        // If no bin file loaded yet, use CSV name
        fileName: prev.fileName || name,
      }));
    } catch (err) {
      setState((prev) => ({
        ...prev,
        loading: false,
        error: `Failed to parse CSV: ${err instanceof Error ? err.message : String(err)}`,
      }));
    }
  }, []);

  const reset = useCallback(() => {
    setState(INITIAL_STATE);
  }, []);

  return {
    frames: state.frames,
    stats: state.stats,
    simSummary: state.simSummary,
    version: state.version,
    fileName: state.fileName,
    loading: state.loading,
    error: state.error,
    loadBinFile,
    loadSimFile,
    reset,
  };
}
