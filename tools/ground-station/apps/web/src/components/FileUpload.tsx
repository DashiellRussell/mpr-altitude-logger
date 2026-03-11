import React, { useState, useRef, useCallback } from 'react';

interface FileUploadProps {
  onBinFile: (buffer: ArrayBuffer, name: string) => void;
  onSimFile: (text: string, name: string) => void;
  loading: boolean;
  error: string | null;
}

export function FileUpload({ onBinFile, onSimFile, loading, error }: FileUploadProps) {
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
      // Reset so the same file can be selected again
      if (inputRef.current) inputRef.current.value = '';
    },
    [processFile],
  );

  return (
    <div className="upload-container">
      <div
        className={`upload-zone${dragOver ? ' drag-over' : ''}`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={() => inputRef.current?.click()}
      >
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
          You can load both — drop the .bin first, then the .csv.
        </p>

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
