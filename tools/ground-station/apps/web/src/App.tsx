import React from 'react';
import { useFlightData } from './hooks/useFlightData';
import { FileUpload } from './components/FileUpload';
import { FlightSummary } from './components/FlightSummary';
import { AltitudeChart } from './components/AltitudeChart';
import { VelocityChart } from './components/VelocityChart';
import { PressureChart } from './components/PressureChart';
import { PowerChart } from './components/PowerChart';
import { StateTimeline } from './components/StateTimeline';
import { SimComparison } from './components/SimComparison';
import { ExportButton } from './components/ExportButton';

export default function App() {
  const {
    frames,
    stats,
    simSummary,
    version,
    fileName,
    loading,
    error,
    loadBinFile,
    loadSimFile,
    reset,
  } = useFlightData();

  if (!stats) {
    return (
      <FileUpload
        onBinFile={loadBinFile}
        onSimFile={loadSimFile}
        loading={loading}
        error={error}
      />
    );
  }

  return (
    <div className="dashboard">
      <div className="dashboard-header">
        <div>
          <h1>MPR Altitude Logger — Flight Review</h1>
          <span className="file-info">
            {fileName} | v{version} | {stats.nFrames} frames
          </span>
        </div>
        <div className="btn-group">
          <ExportButton frames={frames} version={version} />
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
          <button className="btn" onClick={reset}>Reset</button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="dashboard-grid">
        <div className="card">
          <h2>Flight State Timeline</h2>
          <StateTimeline stats={stats} />
        </div>

        <FlightSummary stats={stats} version={version} />

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
              <VelocityChart frames={frames} transitions={stats.transitions} />
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
      </div>
    </div>
  );
}
