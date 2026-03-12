import React, { lazy, Suspense, useState } from 'react';
import { useFlightData } from './hooks/useFlightData';
import { usePlayback } from './hooks/usePlayback';
import { FileUpload } from './components/FileUpload';
import { FlightSummary } from './components/FlightSummary';
import { FlightOverview } from './components/FlightOverview';
import { FlightInsights } from './components/FlightInsights';
import { AltitudeChart } from './components/AltitudeChart';
import { VelocityChart } from './components/VelocityChart';
import { PressureChart } from './components/PressureChart';
import { PowerChart } from './components/PowerChart';
import { StateTimeline } from './components/StateTimeline';
import { SimComparison } from './components/SimComparison';
import { ExportButton } from './components/ExportButton';
import { TelemetryPanel } from './components/TelemetryPanel';
import { PlaybackBar } from './components/PlaybackBar';
import { ChartTabs } from './components/ChartTabs';

// Lazy-load the 3D scene to keep initial bundle small
const RocketScene = lazy(() =>
  import('./components/rocket/RocketScene').then((m) => ({ default: m.RocketScene }))
);

type ViewMode = 'launch' | 'analysis';

export default function App() {
  const {
    frames,
    stats,
    simSummary,
    version,
    fileName,
    loading,
    error,
    discoveredFlights,
    discoveredSims,
    discovering,
    loadBinFile,
    loadDiscoveredFlight,
    loadSimFile,
    loadDiscoveredSim,
    reset,
    refresh,
  } = useFlightData();

  const [playback, controls] = usePlayback(frames);
  const [viewMode, setViewMode] = useState<ViewMode>('launch');

  if (!stats) {
    return (
      <FileUpload
        onBinFile={loadBinFile}
        onSimFile={loadSimFile}
        loading={loading}
        error={error}
        discoveredFlights={discoveredFlights}
        discoveredSims={discoveredSims}
        discovering={discovering}
        onSelectFlight={loadDiscoveredFlight}
        onSelectSim={loadDiscoveredSim}
        onRefresh={refresh}
      />
    );
  }

  const t0 = frames.length > 0 ? frames[0].timestamp_ms : 0;
  const tEnd = frames.length > 0 ? frames[frames.length - 1].timestamp_ms : 0;
  const totalDuration = (tEnd - t0) / 1000;

  const currentFrame = playback.currentFrame;

  return (
    <div className="dashboard">
      <div className="dashboard-header">
        <div>
          <h1>MPR ALTITUDE LOGGER — FLIGHT REVIEW</h1>
          <span className="file-info">
            {fileName} | v{version} | {stats.nFrames} frames
          </span>
        </div>
        <div className="btn-group">
          <ExportButton frames={frames} version={version} />
          {discoveredSims.length > 0 && !simSummary ? (
            <select
              className="btn"
              defaultValue=""
              onChange={(e) => {
                const idx = Number(e.target.value);
                if (!isNaN(idx)) loadDiscoveredSim(discoveredSims[idx]);
                e.target.value = '';
              }}
            >
              <option value="" disabled>+ Sim CSV</option>
              {discoveredSims.map((f, i) => (
                <option key={f.name} value={i}>{f.name}</option>
              ))}
              <option value="browse">Browse...</option>
            </select>
          ) : (
            <button className="btn" onClick={() => {
              const input = document.createElement('input');
              input.type = 'file';
              input.accept = '.csv';
              input.onchange = (e) => {
                const file = (e.target as HTMLInputElement).files?.[0];
                if (!file) return;
                const reader = new FileReader();
                reader.onload = () => loadSimFile(reader.result as string, file.name);
                reader.readAsText(file);
              };
              input.click();
            }}>
              + Sim CSV
            </button>
          )}
          <div className="view-toggle">
            <button
              className={`view-toggle-btn ${viewMode === 'launch' ? 'view-toggle-active' : ''}`}
              onClick={() => setViewMode('launch')}
            >
              Launch View
            </button>
            <button
              className={`view-toggle-btn ${viewMode === 'analysis' ? 'view-toggle-active' : ''}`}
              onClick={() => setViewMode('analysis')}
            >
              Analysis
            </button>
          </div>
          <button className="btn" onClick={reset}>Reset</button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {viewMode === 'launch' ? (
        /* ===== LAUNCH VIEW: SpaceX two-column layout ===== */
        <div className="launch-layout">
          <div className="launch-left">
            <Suspense fallback={<div className="rocket-scene-container rocket-fallback">Loading 3D...</div>}>
              <RocketScene
                state={currentFrame?.state_name ?? 'PAD'}
                altitude={currentFrame?.alt_filtered_m ?? 0}
                velocity={currentFrame?.vel_filtered_ms ?? 0}
                maxAlt={stats.maxAlt}
                isPlaying={playback.isPlaying}
              />
            </Suspense>
          </div>
          <div className="launch-right">
            <TelemetryPanel
              frame={currentFrame}
              maxAlt={stats.maxAlt}
              currentTime={playback.currentTime}
            />
            <div className="chart-tabs-container">
              <ChartTabs
                frames={frames}
                transitions={stats.transitions}
                simSummary={simSummary ?? undefined}
                version={version}
                cursorTime={playback.currentTime}
              />
            </div>
          </div>
          <div className="launch-playback">
            <PlaybackBar
              playback={playback}
              controls={controls}
              stats={stats}
              totalDuration={totalDuration}
            />
          </div>
        </div>
      ) : (
        /* ===== ANALYSIS VIEW: Original vertical card layout ===== */
        <div className="dashboard-grid">
          <div className="card">
            <h2>Flight State Timeline</h2>
            <StateTimeline stats={stats} />
          </div>

          <FlightSummary stats={stats} version={version} />

          <div className="card">
            <h2>Flight Overview — All Channels</h2>
            <div className="chart-container chart-container-tall">
              <FlightOverview
                frames={frames}
                simSummary={simSummary ?? undefined}
                transitions={stats.transitions}
                version={version}
              />
            </div>
          </div>

          <div className="card">
            <h2>Altitude</h2>
            <div className="chart-container">
              <AltitudeChart
                frames={frames}
                simSummary={simSummary ?? undefined}
                transitions={stats.transitions}
              />
            </div>
          </div>

          <div className="charts-row">
            <div className="card">
              <h2>Velocity</h2>
              <div className="chart-container">
                <VelocityChart
                  frames={frames}
                  transitions={stats.transitions}
                  simSummary={simSummary ?? undefined}
                />
              </div>
            </div>
            <div className="card">
              <h2>Pressure</h2>
              <div className="chart-container">
                <PressureChart frames={frames} />
              </div>
            </div>
          </div>

          <div className="card">
            <h2>Power Rails</h2>
            <div className="chart-container">
              <PowerChart frames={frames} version={version} />
            </div>
          </div>

          {simSummary && stats && (
            <SimComparison stats={stats} simSummary={simSummary} />
          )}

          <FlightInsights
            frames={frames}
            stats={stats}
            version={version}
            simSummary={simSummary ?? undefined}
          />
        </div>
      )}
    </div>
  );
}
