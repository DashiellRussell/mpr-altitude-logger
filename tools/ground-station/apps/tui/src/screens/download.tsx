import React, { useState, useEffect, useCallback } from 'react';
import { Box, Text, useInput, useApp } from 'ink';
import Spinner from 'ink-spinner';
import { writeFileSync } from 'fs';
import { usePico } from '../hooks/use-pico.js';
import { listBinFiles, downloadFile } from '../serial/download.js';
import type { BinFileInfo } from '../serial/download.js';
import { Header } from '../components/header.js';
import { StatusDot } from '../components/status-dot.js';
import { KeyBar } from '../components/key-bar.js';

interface DownloadProps {
  port?: string;
  onComplete: (filePath: string) => void;
  onCancel: () => void;
}

type Phase = 'connect' | 'listing' | 'select' | 'downloading' | 'done' | 'error';

/**
 * SD file picker + download progress screen.
 * Lists .bin files on the Pico's SD card, lets user select one,
 * downloads it, and saves locally.
 */
export function Download({ port, onComplete, onCancel }: DownloadProps) {
  const { exit } = useApp();
  const pico = usePico(port);
  const [phase, setPhase] = useState<Phase>('connect');
  const [files, setFiles] = useState<BinFileInfo[]>([]);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [downloadProgress, setDownloadProgress] = useState(0);
  const [downloadTotal, setDownloadTotal] = useState(0);
  const [errorMsg, setErrorMsg] = useState('');
  const [statusMsg, setStatusMsg] = useState('');

  // List files once connected
  const loadFiles = useCallback(async () => {
    if (!pico.connected) return;
    setPhase('listing');
    setStatusMsg('Mounting SD card and listing files...');
    try {
      const binFiles = await listBinFiles(pico.link);
      if (binFiles.length === 0) {
        setErrorMsg('No .bin files found on SD card.');
        setPhase('error');
        return;
      }
      setFiles(binFiles);
      setSelectedIdx(0);
      setPhase('select');
      setStatusMsg('');
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
      setPhase('error');
    }
  }, [pico.connected, pico.link]);

  // Auto-list when connected
  useEffect(() => {
    if (pico.connected && phase === 'connect') {
      loadFiles();
    }
  }, [pico.connected, phase, loadFiles]);

  // Download selected file
  const doDownload = useCallback(async () => {
    if (!pico.connected || !files[selectedIdx]) return;

    const file = files[selectedIdx];
    setPhase('downloading');
    setDownloadTotal(file.size);
    setDownloadProgress(0);
    setStatusMsg(`Downloading ${file.name}...`);

    try {
      const data = await downloadFile(pico.link, file.name, (bytes) => {
        setDownloadProgress(bytes);
      });

      // Save locally
      const localPath = file.name;
      writeFileSync(localPath, data);
      setStatusMsg(`Downloaded ${data.length} bytes, saved to ${localPath}`);
      setPhase('done');

      // Clean up serial before passing to postflight
      await pico.link.close();

      // Notify parent
      setTimeout(() => onComplete(localPath), 500);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
      setPhase('error');
    }
  }, [pico.connected, pico.link, files, selectedIdx, onComplete]);

  // Key handling
  useInput((input, key) => {
    if (input === 'q' || input === 'Q') {
      onCancel();
      return;
    }

    if (phase === 'select') {
      if (key.upArrow && selectedIdx > 0) {
        setSelectedIdx((i) => i - 1);
      }
      if (key.downArrow && selectedIdx < files.length - 1) {
        setSelectedIdx((i) => i + 1);
      }
      if (key.return) {
        doDownload();
      }
    }

    if (phase === 'error' && input === 'r') {
      setErrorMsg('');
      setPhase('connect');
    }
  });

  // Progress bar
  const progressBar = (current: number, total: number, width: number = 40): string => {
    const ratio = total > 0 ? Math.min(1, current / total) : 0;
    const filled = Math.round(ratio * width);
    return '\u2588'.repeat(filled) + '\u2591'.repeat(width - filled);
  };

  return (
    <Box flexDirection="column">
      <Header title="DOWNLOAD FLIGHT LOG" />

      <StatusDot connected={pico.connected} port={pico.portPath} error={pico.error} />
      <Text> </Text>

      {/* Connecting */}
      {phase === 'connect' && !pico.connected && (
        <Text color="yellow">
          {'  '}
          <Spinner type="dots" />
          {' Searching for Pico...'}
        </Text>
      )}

      {/* Listing */}
      {phase === 'listing' && (
        <Text color="yellow">
          {'  '}
          <Spinner type="dots" />
          {' '}{statusMsg}
        </Text>
      )}

      {/* File selection */}
      {phase === 'select' && (
        <Box flexDirection="column">
          <Text bold>  Flight logs on SD card:</Text>
          <Text> </Text>
          {files.map((f, i) => {
            const sizeStr =
              f.size > 1024
                ? `${(f.size / 1024).toFixed(1)} KB`
                : `${f.size} B`;
            const selected = i === selectedIdx;
            return (
              <Text key={f.name}>
                {'  '}
                {selected ? (
                  <Text color="cyan" bold>
                    {'> '}
                    {f.name.padEnd(30)}
                    {sizeStr}
                  </Text>
                ) : (
                  <Text>
                    {'  '}
                    {f.name.padEnd(30)}
                    {sizeStr}
                  </Text>
                )}
              </Text>
            );
          })}
          <Text> </Text>
          <Text dimColor>  Use arrow keys to select, Enter to download</Text>
        </Box>
      )}

      {/* Downloading */}
      {phase === 'downloading' && (
        <Box flexDirection="column">
          <Text color="cyan">
            {'  '}
            <Spinner type="dots" />
            {' '}{statusMsg}
          </Text>
          <Text>
            {'  '}
            <Text color="cyan">{progressBar(downloadProgress, downloadTotal)}</Text>
            {'  '}
            {downloadTotal > 0
              ? `${(downloadProgress / 1024).toFixed(1)} / ${(downloadTotal / 1024).toFixed(1)} KB`
              : `${(downloadProgress / 1024).toFixed(1)} KB`}
          </Text>
        </Box>
      )}

      {/* Done */}
      {phase === 'done' && (
        <Text color="green">  {statusMsg}</Text>
      )}

      {/* Error */}
      {phase === 'error' && (
        <Box flexDirection="column">
          <Text color="red">  Error: {errorMsg}</Text>
          <Text> </Text>
          <Text dimColor>  Press [R] to retry or [Q] to quit</Text>
        </Box>
      )}

      <KeyBar
        keys={
          phase === 'select'
            ? [
                ['\u2191\u2193', 'Navigate'],
                ['\u21b5', 'Download'],
                ['Q', 'Quit'],
              ]
            : phase === 'error'
              ? [
                  ['R', 'Retry'],
                  ['Q', 'Quit'],
                ]
              : [['Q', 'Quit']]
        }
      />
    </Box>
  );
}
