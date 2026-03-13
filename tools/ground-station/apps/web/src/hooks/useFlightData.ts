import { useState, useCallback, useEffect } from 'react';
import type { FlightFrame, FlightStats, SimSummary } from '@mpr/shared';
import {
  decodeBinFile,
  analyzeFlight,
  parseSimCsv,
  isOpenRocketCsv,
  parseOpenRocketCsv,
  summarizeSim,
} from '@mpr/shared';

export interface DiscoveredFile {
  name: string;
  size: number;
  mtime: string;
  source: string;
  folder?: boolean;  // true if this flight lives in a per-flight folder
}

interface FlightDataState {
  frames: FlightFrame[];
  stats: FlightStats | null;
  simSummary: SimSummary | null;
  version: number;
  fileName: string;
  loading: boolean;
  error: string | null;
  discoveredFlights: DiscoveredFile[];
  discoveredSims: DiscoveredFile[];
  discovering: boolean;
}

const INITIAL_STATE: FlightDataState = {
  frames: [],
  stats: null,
  simSummary: null,
  version: 1,
  fileName: '',
  loading: false,
  error: null,
  discoveredFlights: [],
  discoveredSims: [],
  discovering: true,
};

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export { formatSize };

export function useFlightData() {
  const [state, setState] = useState<FlightDataState>(INITIAL_STATE);

  // Auto-discover files on mount
  useEffect(() => {
    let cancelled = false;

    async function discover() {
      try {
        const [flightsRes, simsRes] = await Promise.all([
          fetch('/api/flights'),
          fetch('/api/sims'),
        ]);

        if (cancelled) return;

        const flights: DiscoveredFile[] = flightsRes.ok ? await flightsRes.json() : [];
        const sims: DiscoveredFile[] = simsRes.ok ? await simsRes.json() : [];

        setState((prev) => ({
          ...prev,
          discoveredFlights: flights,
          discoveredSims: sims,
          discovering: false,
        }));
      } catch {
        // API not available (e.g. production build) — fall back to manual upload
        if (!cancelled) {
          setState((prev) => ({ ...prev, discovering: false }));
        }
      }
    }

    discover();
    return () => { cancelled = true; };
  }, []);

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

  /** Pick the best sim file: prefer *_sim.csv, then newest */
  const pickBestSim = useCallback((sims: DiscoveredFile[]): DiscoveredFile | null => {
    if (sims.length === 0) return null;
    const simFile = sims.find((f) => f.name.includes('_sim'));
    return simFile ?? sims[0];
  }, []);

  /** Fetch and parse a sim CSV from the API */
  const fetchAndParseSim = useCallback(async (file: DiscoveredFile): Promise<SimSummary | null> => {
    try {
      const url = `/api/sims/${encodeURIComponent(file.name)}`;
      const res = await fetch(url);
      if (!res.ok) return null;
      const text = await res.text();
      const isOR = isOpenRocketCsv(text);
      const rows = isOR ? parseOpenRocketCsv(text).rows : parseSimCsv(text);
      if (rows.length === 0) return null;
      return summarizeSim(rows);
    } catch {
      return null;
    }
  }, []);

  const loadDiscoveredFlight = useCallback(async (file: DiscoveredFile) => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const folderParam = file.folder ? '&folder=true' : '';
      const url = `/api/flights/${encodeURIComponent(file.name)}?source=${encodeURIComponent(file.source)}${folderParam}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const buffer = await res.arrayBuffer();

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

      // Auto-load best sim if available
      let simSummary: SimSummary | null = null;
      const currentSims = state.discoveredSims;
      const bestSim = pickBestSim(currentSims);
      if (bestSim) {
        simSummary = await fetchAndParseSim(bestSim);
      }

      setState((prev) => ({
        ...prev,
        frames: decoded.frames,
        stats,
        version: decoded.version,
        fileName: file.name,
        simSummary: simSummary ?? prev.simSummary,
        loading: false,
      }));
    } catch (err) {
      setState((prev) => ({
        ...prev,
        loading: false,
        error: `Failed to load flight: ${err instanceof Error ? err.message : String(err)}`,
      }));
    }
  }, [state.discoveredSims, pickBestSim, fetchAndParseSim]);

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

  const loadDiscoveredSim = useCallback(async (file: DiscoveredFile) => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const url = `/api/sims/${encodeURIComponent(file.name)}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const text = await res.text();

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
        fileName: prev.fileName || file.name,
      }));
    } catch (err) {
      setState((prev) => ({
        ...prev,
        loading: false,
        error: `Failed to load sim: ${err instanceof Error ? err.message : String(err)}`,
      }));
    }
  }, []);

  const reset = useCallback(() => {
    setState((prev) => ({
      ...INITIAL_STATE,
      discoveredFlights: prev.discoveredFlights,
      discoveredSims: prev.discoveredSims,
      discovering: false,
    }));
  }, []);

  const refresh = useCallback(async () => {
    setState((prev) => ({ ...prev, discovering: true }));
    try {
      const [flightsRes, simsRes] = await Promise.all([
        fetch('/api/flights'),
        fetch('/api/sims'),
      ]);
      const flights: DiscoveredFile[] = flightsRes.ok ? await flightsRes.json() : [];
      const sims: DiscoveredFile[] = simsRes.ok ? await simsRes.json() : [];
      setState((prev) => ({
        ...prev,
        discoveredFlights: flights,
        discoveredSims: sims,
        discovering: false,
      }));
    } catch {
      setState((prev) => ({ ...prev, discovering: false }));
    }
  }, []);

  return {
    frames: state.frames,
    stats: state.stats,
    simSummary: state.simSummary,
    version: state.version,
    fileName: state.fileName,
    loading: state.loading,
    error: state.error,
    discoveredFlights: state.discoveredFlights,
    discoveredSims: state.discoveredSims,
    discovering: state.discovering,
    loadBinFile,
    loadDiscoveredFlight,
    loadSimFile,
    loadDiscoveredSim,
    reset,
    refresh,
  };
}
