import React, { useState, useRef, useCallback } from 'react';
import type { DiscoveredFile } from '../hooks/useFlightData';
import { formatSize } from '../hooks/useFlightData';

interface FileUploadProps {
  onBinFile: (buffer: ArrayBuffer, name: string) => void;
  onSimFile: (text: string, name: string) => void;
  loading: boolean;
  error: string | null;
  discoveredFlights: DiscoveredFile[];
  discoveredSims: DiscoveredFile[];
  discovering: boolean;
  onSelectFlight: (file: DiscoveredFile) => void;
  onSelectSim: (file: DiscoveredFile) => void;
  onRefresh: () => void;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function sourceLabel(source: string): string {
  return source === 'local' ? 'flights/' : source;
}

export function FileUpload({
  onBinFile,
  onSimFile,
  loading,
  error,
  discoveredFlights,
  discoveredSims,
  discovering,
  onSelectFlight,
  onSelectSim,
  onRefresh,
}: FileUploadProps) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const processFile = useCallback(
    (file: File) => {
      const ext = file.name.split('.').pop()?.toLowerCase();
      if (ext === 'bin') {
        const reader = new FileReader();
        reader.onload = () => {
          if (reader.result instanceof ArrayBuffer) {
            onBinFile(reader.result, file.name);
          }
        };
        reader.readAsArrayBuffer(file);
      } else if (ext === 'csv') {
        const reader = new FileReader();
        reader.onload = () => {
          if (typeof reader.result === 'string') {
            onSimFile(reader.result, file.name);
          }
        };
        reader.readAsText(file);
      }
    },
    [onBinFile, onSimFile],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const files = Array.from(e.dataTransfer.files);
      for (const file of files) {
        processFile(file);
      }
    },
    [processFile],
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setDragOver(false);
  }, []);

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files ?? []);
      for (const file of files) {
        processFile(file);
      }
      if (inputRef.current) inputRef.current.value = '';
    },
    [processFile],
  );

  const hasDiscovered = discoveredFlights.length > 0 || discoveredSims.length > 0;

  return (
    <div className="upload-container">
      {/* Auto-discovered files */}
      {(hasDiscovered || discovering) && (
        <div className="discovered-files">
          <div className="discovered-header">
            <h2>Flight Files</h2>
            <button
              className="btn btn-sm"
              onClick={onRefresh}
              disabled={discovering}
            >
              {discovering ? 'Scanning...' : 'Rescan'}
            </button>
          </div>

          {discovering && !hasDiscovered && (
            <div className="discovering">Scanning for flight logs...</div>
          )}

          {discoveredFlights.length > 0 && (
            <div className="file-list">
              <div className="file-list-label">Flight Logs (.bin)</div>
              {discoveredFlights.map((f) => (
                <button
                  key={`${f.source}/${f.name}`}
                  className="file-item"
                  onClick={() => onSelectFlight(f)}
                  disabled={loading}
                >
                  <span className="file-name">{f.name}</span>
                  <span className="file-meta">
                    <span className="file-size">{formatSize(f.size)}</span>
                    <span className="file-date">{formatDate(f.mtime)}</span>
                    <span className="file-source">{sourceLabel(f.source)}</span>
                  </span>
                </button>
              ))}
            </div>
          )}

          {discoveredSims.length > 0 && (
            <div className="file-list">
              <div className="file-list-label">Simulation Data (.csv)</div>
              {discoveredSims.map((f) => (
                <button
                  key={`${f.source}/${f.name}`}
                  className="file-item file-item-sim"
                  onClick={() => onSelectSim(f)}
                  disabled={loading}
                >
                  <span className="file-name">{f.name}</span>
                  <span className="file-meta">
                    <span className="file-size">{formatSize(f.size)}</span>
                    <span className="file-date">{formatDate(f.mtime)}</span>
                  </span>
                </button>
              ))}
            </div>
          )}

          {hasDiscovered && <div className="divider-label">or drop files manually</div>}
        </div>
      )}

      {/* Drag-and-drop zone */}
      <div
        className={`upload-zone${dragOver ? ' drag-over' : ''}${hasDiscovered ? ' upload-zone-compact' : ''}`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={() => inputRef.current?.click()}
      >
        {!hasDiscovered && (
          <>
            <div className="upload-icon">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#4a9eff" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="17 8 12 3 7 8" />
                <line x1="12" y1="3" x2="12" y2="15" />
              </svg>
            </div>
            <h2>Drop .bin or .csv files here</h2>
            <p>
              Upload a binary flight log (.bin) for analysis,
              or a simulation CSV (.csv) for comparison overlay.
            </p>
          </>
        )}

        {hasDiscovered && (
          <p className="upload-zone-hint">Drop .bin or .csv files here, or click to browse</p>
        )}

        {loading && <div className="loading">Decoding...</div>}
        {error && <div className="error">{error}</div>}

        <button
          className="upload-btn"
          onClick={(e) => {
            e.stopPropagation();
            inputRef.current?.click();
          }}
        >
          Browse Files
        </button>

        <input
          ref={inputRef}
          type="file"
          accept=".bin,.csv"
          multiple
          style={{ display: 'none' }}
          onChange={handleInputChange}
        />
      </div>
    </div>
  );
}
