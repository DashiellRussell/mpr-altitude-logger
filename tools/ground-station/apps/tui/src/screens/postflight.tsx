import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Box, Text, useInput, useApp } from 'ink';
import Spinner from 'ink-spinner';
import { writeFileSync, readFileSync, readdirSync, statSync, existsSync, copyFileSync, mkdirSync, rmSync } from 'fs';
import { join, resolve, basename } from 'path';
import { homedir, tmpdir } from 'os';
import { execSync } from 'child_process';
import { framesToCsv, sparkline, generateFlightReport } from '@mpr/shared';
import { useFlightData } from '../hooks/use-flight-data.js';
import { Header } from '../components/header.js';
import { Panel } from '../components/panel.js';
import { FlightSummary } from '../components/flight-summary.js';
import { AltitudeChart } from '../components/altitude-chart.js';
import { StateTimeline } from '../components/state-timeline.js';
import { SimCompare } from '../components/sim-compare.js';
import { KeyBar } from '../components/key-bar.js';
import { LogViewer } from '../components/log-viewer.js';
import { DiagnosticsPanel } from '../components/diagnostics-panel.js';

const DASH_WIDTH = 120;

interface PostflightProps {
  binFile?: string;
  simFile?: string;
  port?: string;
}

// ── SD card and file auto-discovery ──────────────────────────────

/** Known system volumes to skip */
const SYSTEM_VOLUMES = new Set([
  'Macintosh HD', 'Macintosh HD - Data', 'Recovery', 'Preboot', 'VM',
  'Update', 'com.apple.TimeMachine.localsnapshots',
]);

/** Scan a directory for flight folders (subdirs containing flight.bin) */
function scanForFlightFolders(dir: string, source: string): FoundFile[] {
  const results: FoundFile[] = [];
  try {
    const entries = readdirSync(dir);
    for (const entry of entries) {
      if (entry.startsWith('.')) continue;
      const entryPath = join(dir, entry);
      try {
        if (!statSync(entryPath).isDirectory()) continue;
        const binPath = join(entryPath, 'flight.bin');
        if (existsSync(binPath)) {
          const fStat = statSync(binPath);
          if (fStat.size > 50) {
            const hasPreflight = existsSync(join(entryPath, 'preflight.txt'));
            const hasCrashTxt = existsSync(join(entryPath, 'crash.txt'));
            // Read log version from binary header (bytes 6-7, u16 LE)
            let logVersion: number | null = null;
            try {
              const fd = readFileSync(binPath);
              if (fd.length >= 10 && fd.toString('ascii', 0, 6) === 'RKTLOG') {
                logVersion = fd.readUInt16LE(6);
              }
            } catch { /* skip */ }
            let isCrashReboot = false;
            let fwVersion: string | null = null;
            if (hasPreflight) {
              try {
                const pf = readFileSync(join(entryPath, 'preflight.txt'), 'utf-8');
                isCrashReboot = pf.includes('Crash reboot: YES');
                const verMatch = pf.match(/Avionics v([^\s\n]+)/);
                if (verMatch) fwVersion = verMatch[1];
              } catch { /* skip */ }
            }
            results.push({
              path: binPath,
              name: entry,
              size: fStat.size,
              mtime: fStat.mtime,
              source,
              folder: entry,
              hasPreflight,
              isCrashReboot,
              hasCrashTxt,
              fwVersion,
              logVersion,
            });
          }
        }
      } catch { /* skip unreadable entries */ }
    }
  } catch { /* skip unreadable dir */ }
  return results;
}

/** Scan a directory for legacy flat .bin files */
function scanForFlatBinFiles(dir: string, source: string): FoundFile[] {
  const results: FoundFile[] = [];
  try {
    const files = readdirSync(dir).filter(f => f.endsWith('.bin'));
    for (const f of files) {
      const fPath = join(dir, f);
      try {
        const fStat = statSync(fPath);
        if (fStat.size > 50) {
          results.push({ path: fPath, name: f, size: fStat.size, mtime: fStat.mtime, source, folder: null, hasPreflight: false, isCrashReboot: false, hasCrashTxt: false, fwVersion: null, logVersion: null });
        }
      } catch { /* skip unreadable files */ }
    }
  } catch { /* skip */ }
  return results;
}

/** Scan /Volumes/ for mounted SD cards / USB drives containing flight logs */
function findBinFilesOnVolumes(): FoundFile[] {
  const results: FoundFile[] = [];

  if (!existsSync('/Volumes')) return results;

  try {
    const volumes = readdirSync('/Volumes').filter(v => !SYSTEM_VOLUMES.has(v) && !v.startsWith('.'));

    for (const vol of volumes) {
      const volPath = join('/Volumes', vol);
      try {
        const st = statSync(volPath);
        if (!st.isDirectory()) continue;

        const sdSource = `SD: ${vol}`;

        // Scan for flight folders (new per-flight layout)
        results.push(...scanForFlightFolders(volPath, sdSource));

        // Scan root for legacy flat .bin files
        results.push(...scanForFlatBinFiles(volPath, sdSource));

        // Also check /sd subdirectory (some card readers mount the Pico's filesystem)
        const sdPath = join(volPath, 'sd');
        if (existsSync(sdPath)) {
          results.push(...scanForFlightFolders(sdPath, sdSource));
          results.push(...scanForFlatBinFiles(sdPath, sdSource));
        }
      } catch { /* skip inaccessible volumes */ }
    }
  } catch { /* /Volumes not readable */ }

  // Sort by folder name ascending for session grouping
  results.sort((a, b) => {
    if (a.folder && b.folder) return a.folder.localeCompare(b.folder);
    if (a.folder && !b.folder) return -1;
    if (!a.folder && b.folder) return 1;
    return a.mtime.getTime() - b.mtime.getTime();
  });

  // Group sequential crash reboots into sessions
  // A session starts with a normal boot and includes all following crash reboots
  let sessionLabel: string | undefined;
  for (const f of results) {
    if (f.folder && !f.isCrashReboot) {
      // Normal boot — starts a new session
      sessionLabel = f.folder;
      f.sessionGroup = undefined; // session leader, no indent
    } else if (f.folder && f.isCrashReboot && sessionLabel) {
      // Crash reboot — belongs to the previous session
      f.sessionGroup = sessionLabel;
    }
  }

  // Re-sort descending for display (newest first)
  results.sort((a, b) => {
    if (a.folder && b.folder) return b.folder.localeCompare(a.folder);
    if (a.folder && !b.folder) return -1;
    if (!a.folder && b.folder) return 1;
    return b.mtime.getTime() - a.mtime.getTime();
  });
  return results;
}

/** Find the flights/ directory in the repo for storing copies */
function getFlightsDir(): string {
  // Walk up from cwd looking for the repo root (has tools/ or avionics/ dir)
  let dir = resolve(process.cwd());
  for (let i = 0; i < 5; i++) {
    if (existsSync(join(dir, 'flights'))) return join(dir, 'flights');
    if (existsSync(join(dir, 'avionics')) || existsSync(join(dir, 'tools'))) {
      // Found repo root — flights/ goes here
      return join(dir, 'flights');
    }
    const parent = resolve(dir, '..');
    if (parent === dir) break;
    dir = parent;
  }
  // Fallback: create flights/ next to cwd
  return resolve(process.cwd(), 'flights');
}

/** Return a path that doesn't collide with existing files, appending (1), (2), etc. */
function uniquePath(filePath: string): string {
  if (!existsSync(filePath)) return filePath;
  const dot = filePath.lastIndexOf('.');
  const base = dot > 0 ? filePath.slice(0, dot) : filePath;
  const ext = dot > 0 ? filePath.slice(dot) : '';
  let n = 1;
  while (existsSync(`${base} (${n})${ext}`)) n++;
  return `${base} (${n})${ext}`;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ── File picker component ─────────────────────────────────────

interface FoundFile {
  path: string;
  name: string;
  size: number;
  mtime: Date;
  source: string;             // e.g. "SD: ROCKET" or "local"
  folder: string | null;       // folder name (e.g. "flight_001") or null for legacy flat files
  hasPreflight: boolean;       // true if preflight.txt exists alongside the .bin
  isCrashReboot: boolean;      // true if preflight.txt says "Crash reboot: YES"
  hasCrashTxt: boolean;        // true if crash.txt exists in the flight folder
  fwVersion: string | null;    // firmware version from preflight.txt (e.g. "1.14.0")
  logVersion: number | null;   // binary log format version from file header (1, 2, or 3)
  sessionGroup?: string;       // groups sequential crash reboots under one label
}

/** Scan local flights/ directory for .bin files */
function findLocalBinFiles(): FoundFile[] {
  const results: FoundFile[] = [];
  const flightsDir = getFlightsDir();
  if (!existsSync(flightsDir)) return results;
  try {
    const files = readdirSync(flightsDir).filter(f => f.endsWith('.bin'));
    for (const f of files) {
      const fPath = join(flightsDir, f);
      try {
        const fStat = statSync(fPath);
        if (fStat.size > 50) {
          results.push({
            path: fPath,
            name: f,
            size: fStat.size,
            mtime: fStat.mtime,
            source: 'local',
            folder: null,
            hasPreflight: false,
            isCrashReboot: false,
            hasCrashTxt: false,
            fwVersion: null,
            logVersion: null,
          });
        }
      } catch { /* skip */ }
    }
  } catch { /* skip */ }
  // Sort by mtime descending (newest first)
  results.sort((a, b) => b.mtime.getTime() - a.mtime.getTime());
  return results;
}

function PostflightFilePicker({ onReady }: { onReady: (binPath: string) => void }) {
  const { exit } = useApp();
  const [files, setFiles] = useState<FoundFile[]>([]);
  const [selected, setSelected] = useState(0);
  const [scanning, setScanning] = useState(true);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);

  // Scan on mount
  useEffect(() => {
    const timer = setTimeout(() => {
      // Gather SD card files (source already set by scanner)
      const sdFiles = findBinFilesOnVolumes();

      // Gather local flights/ files
      const localFiles = findLocalBinFiles();

      // Merge: SD files first, then local (deduplicate by size+name)
      const seen = new Set<string>();
      const merged: FoundFile[] = [];
      for (const f of sdFiles) {
        merged.push(f);
        seen.add(`${f.name}:${f.size}`);
      }
      for (const f of localFiles) {
        // Skip if a same-name+size file already came from SD
        const key = `${f.name}:${f.size}`;
        if (!seen.has(key)) {
          merged.push(f);
        }
      }

      setFiles(merged);
      setScanning(false);
    }, 300);
    return () => clearTimeout(timer);
  }, []);

  const loadFile = useCallback((file: FoundFile) => {
    // If the file is already local, just open it
    if (file.source === 'local') {
      onReady(file.path);
      return;
    }

    // Copy from SD to local flights/
    setCopyStatus('Copying...');
    setTimeout(() => {
      try {
        const flightsDir = getFlightsDir();
        if (!existsSync(flightsDir)) {
          mkdirSync(flightsDir, { recursive: true });
        }

        // Extract volume name from source (e.g. "SD: ROCKET" → "ROCKET")
        const volName = file.source.replace(/^SD:\s*/, '');
        const destName = file.folder
          ? `${volName}_${file.folder}.bin`
          : `${volName}_${file.name}`;
        let destPath = join(flightsDir, destName);

        let needsCopy = true;
        if (existsSync(destPath)) {
          const existingStat = statSync(destPath);
          if (existingStat.size === file.size) {
            needsCopy = false;
          } else {
            destPath = uniquePath(destPath);
          }
        }

        if (needsCopy) {
          copyFileSync(file.path, destPath);
        }

        setCopyStatus(null);
        onReady(destPath);
      } catch (e) {
        setCopyStatus(`Copy failed: ${e instanceof Error ? e.message : String(e)} — reading from SD`);
        setTimeout(() => {
          setCopyStatus(null);
          onReady(file.path);
        }, 1500);
      }
    }, 100);
  }, [onReady]);

  useInput((input, key) => {
    if (input === 'q' || input === 'Q') exit();
    if (copyStatus) return; // busy copying

    if (key.upArrow && selected > 0) setSelected(s => s - 1);
    if (key.downArrow && selected < files.length - 1) setSelected(s => s + 1);
    if (key.return && files.length > 0) {
      loadFile(files[selected]);
    }
  });

  return (
    <Box flexDirection="column" width={DASH_WIDTH + 2}>
      <Header title="POST-FLIGHT ANALYSIS" width={DASH_WIDTH} />

      {scanning ? (
        <Panel title="SCANNING" width={DASH_WIDTH} borderColor="cyan">
          <Text color="yellow">  <Spinner type="dots" /> Scanning for flight logs...</Text>
        </Panel>
      ) : files.length === 0 ? (
        <Panel title="NO FLIGHT LOGS FOUND" width={DASH_WIDTH} borderColor="red">
          <Text color="red">  No .bin files found on SD card or in flights/ directory.</Text>
          <Text dimColor>  Insert SD card or run: pnpm dev:tui -- postflight {'<file.bin>'}</Text>
        </Panel>
      ) : (
        <Panel title="SELECT FLIGHT LOG" width={DASH_WIDTH} borderColor="yellow">
          <Text dimColor>  {files.length} flight log{files.length !== 1 ? 's' : ''} found</Text>
          <Text>{' '}</Text>
          {files.map((f, i) => {
            const date = f.mtime.toLocaleDateString() + ' ' + f.mtime.toLocaleTimeString();
            const prefix = i === selected ? ' \u25b6 ' : '   ';
            const displayName = f.folder ?? f.name;
            const isSD = f.source.startsWith('SD:');
            // Session grouping: indent crash reboots under their parent session
            const indent = f.sessionGroup ? '  \u2514 ' : '';
            const crashTag = f.isCrashReboot ? ' [crash]' : f.hasCrashTxt ? ' [has crash]' : '';
            const preflightTag = f.hasPreflight && !f.isCrashReboot ? ' [preflight]' : '';
            const verTag = f.logVersion ? `v${f.logVersion}` : '';
            const fwTag = f.fwVersion ? `fw${f.fwVersion}` : '';
            const tags = [crashTag, preflightTag].filter(Boolean).join('');
            const verStr = [verTag, fwTag].filter(Boolean).join(' ');
            return (
              <Text key={`${f.path}-${i}`} color={i === selected ? 'cyan' : undefined} bold={i === selected}>
                {prefix}{indent}{(displayName + tags).padEnd(indent ? 28 : 32)}{formatSize(f.size).padEnd(10)}{verStr.padEnd(10)}{date.padEnd(22)}
                <Text color={isSD ? 'green' : 'gray'}>{isSD ? '\u25cf ' : '  '}{f.source}</Text>
              </Text>
            );
          })}
          <Text>{' '}</Text>
          {copyStatus && (
            <Text color="yellow">  <Spinner type="dots" /> {copyStatus}</Text>
          )}
        </Panel>
      )}

      <KeyBar
        keys={
          files.length > 0 && !scanning
            ? [['\u2191\u2193', 'Select'], ['Enter', 'Load'], ['Q', 'Quit']]
            : [['Q', 'Quit']]
        }
        width={DASH_WIDTH}
      />
    </Box>
  );
}

// ── Main postflight component ────────────────────────────────────

export function Postflight({ binFile, simFile, port }: PostflightProps) {
  const [resolvedFile, setResolvedFile] = useState<string | undefined>(binFile);
  // Key increments to force PostflightDashboard remount (resets all internal state)
  const [dashKey, setDashKey] = useState(0);

  const handleBack = useCallback(() => {
    setResolvedFile(undefined);
  }, []);

  const handleReady = useCallback((path: string) => {
    setDashKey(k => k + 1);
    setResolvedFile(path);
  }, []);

  // If a bin file was given directly, go straight to dashboard
  if (resolvedFile) {
    return <PostflightDashboard key={dashKey} binFile={resolvedFile} simFile={simFile} onSwitchFile={handleBack} />;
  }

  // Otherwise, show the auto-discovery / file picker
  return <PostflightFilePicker onReady={handleReady} />;
}

// ── Dashboard component ──────────────────────────────────────────

interface DashboardProps {
  binFile: string;
  simFile?: string;
  onSwitchFile?: () => void;
}

function PostflightDashboard({ binFile, simFile, onSwitchFile }: DashboardProps) {
  const { exit } = useApp();
  const { loading, error, frames, stats, sim, version, skippedBytes } = useFlightData(
    binFile,
    simFile
  );
  const [statusMsg, setStatusMsg] = useState('');
  const [exporting, setExporting] = useState(false);
  const [showLogViewer, setShowLogViewer] = useState(false);
  const csvWritten = useRef(false);

  // Progressive reveal: sections appear one by one
  const [revealStage, setRevealStage] = useState(0);
  // Chart diagonal sweep: 0 → CHART_WIDTH + CHART_HEIGHT
  const [chartSweep, setChartSweep] = useState(0);
  const CHART_WIDTH = 60;
  const CHART_HEIGHT = 18;
  const animStarted = useRef(false);

  // Single animation sequence — runs once when data is ready
  useEffect(() => {
    if (!stats || !frames.length || animStarted.current) return;
    animStarted.current = true;

    const timers: ReturnType<typeof setTimeout>[] = [];
    let t = 0;

    // Stage 1: flight summary
    t += 200;
    timers.push(setTimeout(() => setRevealStage(1), t));

    // Stage 2: show chart, diagonal sweep from bottom-left to top-right
    t += 400;
    const sweepMax = CHART_WIDTH + CHART_HEIGHT;
    timers.push(setTimeout(() => {
      setRevealStage(2);
      let pos = 0;
      const interval = setInterval(() => {
        pos += 3;
        if (pos >= sweepMax) {
          pos = sweepMax;
          clearInterval(interval);
          // After sweep completes, reveal remaining sections
          setTimeout(() => setRevealStage(3), 100);
          setTimeout(() => setRevealStage(4), 300);
          setTimeout(() => setRevealStage(5), 500);
        }
        setChartSweep(pos);
      }, 25);
    }, t));

    return () => timers.forEach(clearTimeout);
  }, [stats, frames.length]);

  // Auto-generate CSV + flight report alongside the .bin when data loads
  useEffect(() => {
    if (!frames.length || !stats || csvWritten.current) return;
    csvWritten.current = true;
    const generated: string[] = [];
    try {
      const csvPath = binFile.replace(/\.bin$/i, '.csv');
      if (csvPath !== binFile && !existsSync(csvPath)) {
        writeFileSync(csvPath, framesToCsv(frames, version));
        generated.push(basename(csvPath));
      }
      const reportPath = binFile.replace(/\.bin$/i, '_report.txt');
      if (reportPath !== binFile && !existsSync(reportPath)) {
        writeFileSync(reportPath, generateFlightReport(frames, stats, version, basename(binFile), sim));
        generated.push(basename(reportPath));
      }
      if (generated.length) {
        setStatusMsg(`Auto-exported ${generated.join(' + ')}`);
        setTimeout(() => setStatusMsg(''), 3000);
      }
    } catch { /* non-critical */ }
  }, [frames.length, stats]);

  useInput((input, _key) => {
    if (showLogViewer) return; // log viewer handles its own input
    if (input === 'q' || input === 'Q') {
      exit();
    }
    if ((input === 'f' || input === 'F') && onSwitchFile) {
      onSwitchFile();
      return;
    }
    if ((input === 'l' || input === 'L') && frames.length) {
      setShowLogViewer(true);
    }
    if ((input === 'e' || input === 'E') && stats && frames.length && !exporting) {
      const csvPath = binFile.replace(/\.bin$/i, '.csv') || 'flight_export.csv';
      setExporting(true);
      setStatusMsg('Exporting CSV...');
      // Brief delay for visual feedback
      setTimeout(() => {
        try {
          const csv = framesToCsv(frames, version);
          writeFileSync(csvPath, csv);
          setStatusMsg(`\u2714 Exported ${frames.length} frames to ${basename(csvPath)}`);
        } catch (e) {
          setStatusMsg(`\u2718 Export error: ${e instanceof Error ? e.message : String(e)}`);
        }
        setExporting(false);
      }, 300);
    }
    if ((input === 'd' || input === 'D') && stats && frames.length && !exporting) {
      setExporting(true);
      setStatusMsg('Exporting to Desktop...');
      setTimeout(() => {
        try {
          const desktop = join(homedir(), 'Desktop');
          const flightName = basename(binFile, '.bin');

          // Build files in a temp folder, then zip
          const tmpBase = join(tmpdir(), `mpr_export_${Date.now()}`);
          const tmpFolder = join(tmpBase, flightName);
          mkdirSync(tmpFolder, { recursive: true });

          copyFileSync(binFile, join(tmpFolder, `${flightName}.bin`));
          writeFileSync(join(tmpFolder, `${flightName}.csv`), framesToCsv(frames, version));
          writeFileSync(join(tmpFolder, `${flightName}_report.txt`),
            generateFlightReport(frames, stats, version, basename(binFile), sim));

          // Look for preflight.txt — could be alongside local copy or on the SD card
          // Strip " (N)" dedup suffix from basename for SD card folder matching
          const cleanName = basename(binFile, '.bin').replace(/\s*\(\d+\)$/, '');
          const preflightCandidates = [
            join(resolve(binFile, '..'), 'preflight.txt'),          // same dir as local .bin
            binFile.replace(/[^/]+\.bin$/i, 'preflight.txt'),       // replace filename
          ];
          // Also check mounted volumes for the original flight folder
          if (existsSync('/Volumes')) {
            try {
              for (const vol of readdirSync('/Volumes')) {
                const volPath = join('/Volumes', vol);
                // Try matching folder names from the bin filename
                const match = cleanName.match(/^.+?_(.+)$/);
                if (match) {
                  preflightCandidates.push(join(volPath, match[1], 'preflight.txt'));
                }
              }
            } catch { /* skip */ }
          }
          for (const src of preflightCandidates) {
            if (existsSync(src)) {
              try {
                // Verify it's a text file (not binary garbage)
                const content = readFileSync(src, 'utf-8');
                if (content.includes('Preflight') || content.includes('UNSW') || content.includes('Avionics')) {
                  writeFileSync(join(tmpFolder, 'preflight.txt'), content);
                  break;
                }
              } catch { /* skip binary/unreadable files */ }
            }
          }

          // Look for boot.txt — same search pattern as preflight.txt
          const bootCandidates = [
            join(resolve(binFile, '..'), 'boot.txt'),
            binFile.replace(/[^/]+\.bin$/i, 'boot.txt'),
          ];
          if (existsSync('/Volumes')) {
            try {
              for (const vol of readdirSync('/Volumes')) {
                const volPath = join('/Volumes', vol);
                const match = cleanName.match(/^.+?_(.+)$/);
                if (match) {
                  bootCandidates.push(join(volPath, match[1], 'boot.txt'));
                }
              }
            } catch { /* skip */ }
          }
          for (const src of bootCandidates) {
            if (existsSync(src)) {
              try {
                const content = readFileSync(src, 'utf-8');
                if (content.length > 0) {
                  writeFileSync(join(tmpFolder, 'boot.txt'), content);
                  break;
                }
              } catch { /* skip binary/unreadable files */ }
            }
          }

          // Look for crash.txt
          const crashCandidates = [
            join(resolve(binFile, '..'), 'crash.txt'),
            binFile.replace(/[^/]+\.bin$/i, 'crash.txt'),
          ];
          if (existsSync('/Volumes')) {
            try {
              for (const vol of readdirSync('/Volumes')) {
                const volPath = join('/Volumes', vol);
                const match = cleanName.match(/^.+?_(.+)$/);
                if (match) {
                  crashCandidates.push(join(volPath, match[1], 'crash.txt'));
                }
              }
            } catch { /* skip */ }
          }
          for (const src of crashCandidates) {
            if (existsSync(src)) {
              try {
                const content = readFileSync(src, 'utf-8');
                if (content.includes('CRASH') || content.includes('WDT')) {
                  writeFileSync(join(tmpFolder, 'crash.txt'), content);
                  break;
                }
              } catch { /* skip */ }
            }
          }

          const zipPath = uniquePath(join(desktop, `${flightName}.zip`));
          execSync(`cd "${tmpBase}" && zip -r "${zipPath}" "${flightName}"`, { stdio: 'pipe' });

          // Clean up temp
          rmSync(tmpBase, { recursive: true, force: true });

          setStatusMsg(`\u2714 Exported ${basename(zipPath)} to ~/Desktop/`);
        } catch (e) {
          setStatusMsg(`\u2718 Export error: ${e instanceof Error ? e.message : String(e)}`);
        }
        setExporting(false);
      }, 300);
    }
    if ((input === 's' || input === 'S') && stats && frames.length && !exporting) {
      const reportPath = binFile.replace(/\.bin$/i, '_report.txt') || 'flight_report.txt';
      setExporting(true);
      setStatusMsg('Saving report...');
      setTimeout(() => {
        try {
          const report = generateFlightReport(frames, stats, version, basename(binFile), sim);
          writeFileSync(reportPath, report);
          setStatusMsg(`\u2714 Saved report to ${basename(reportPath)}`);
        } catch (e) {
          setStatusMsg(`\u2718 Save error: ${e instanceof Error ? e.message : String(e)}`);
        }
        setExporting(false);
      }, 300);
    }
  });

  if (loading) {
    return (
      <Box flexDirection="column" width={DASH_WIDTH + 2}>
        <Header title="POST-FLIGHT ANALYSIS" width={DASH_WIDTH} />
        <Text color="yellow">  <Spinner type="dots" /> Decoding {basename(binFile)}...</Text>
      </Box>
    );
  }

  if (error && !stats) {
    return (
      <Box flexDirection="column" width={DASH_WIDTH + 2}>
        <Header title="POST-FLIGHT ANALYSIS" width={DASH_WIDTH} />
        <Text color="red">  Error: {error}</Text>
        <KeyBar keys={[['Q', 'Quit']]} width={DASH_WIDTH} />
      </Box>
    );
  }

  if (!stats || !frames.length) {
    return (
      <Box flexDirection="column" width={DASH_WIDTH + 2}>
        <Header title="POST-FLIGHT ANALYSIS" width={DASH_WIDTH} />
        <Text color="red">  No valid frames found in {basename(binFile)}</Text>
        <KeyBar keys={[['Q', 'Quit']]} width={DASH_WIDTH} />
      </Box>
    );
  }

  // Build velocity sparkline
  const velocities = frames.map((f) => f.vel_filtered_ms);
  const velSpark = sparkline(velocities, 55);

  // Power rail ranges
  let powerContent: React.ReactNode = null;
  if (stats.version >= 2 && stats.v3v3Range && stats.v5vRange && stats.v9vRange) {
    const mn3 = stats.v3v3Range[0] / 1000;
    const mx3 = stats.v3v3Range[1] / 1000;
    const mn5 = stats.v5vRange[0] / 1000;
    const mx5 = stats.v5vRange[1] / 1000;
    const mn9 = stats.v9vRange[0] / 1000;
    const mx9 = stats.v9vRange[1] / 1000;

    powerContent = (
      <Box flexDirection="column" marginBottom={1}>
        <Text bold>POWER RAILS</Text>
        <Text>
          {'  3V3  '}
          <Text color={mn3 > 3.0 ? 'green' : 'red'}>
            {mn3.toFixed(2)}V--{mx3.toFixed(2)}V
          </Text>
          {'  '}{mn3 > 3.0 ? 'OK' : 'LOW'}
          {'     5V  '}
          <Text color={mn5 > 4.5 ? 'green' : 'red'}>
            {mn5.toFixed(2)}V--{mx5.toFixed(2)}V
          </Text>
          {'  '}{mn5 > 4.5 ? 'OK' : 'LOW'}
          {'     9V  '}
          <Text color={mn9 > 8.0 ? 'green' : 'red'}>
            {mn9.toFixed(2)}V--{mx9.toFixed(2)}V
          </Text>
          {'  '}{mn9 > 8.0 ? 'OK' : 'LOW'}
        </Text>
      </Box>
    );
  } else if (stats.vBattRange) {
    const mn = stats.vBattRange[0] / 1000;
    const mx = stats.vBattRange[1] / 1000;
    powerContent = (
      <Box flexDirection="column" marginBottom={1}>
        <Text bold>POWER RAILS</Text>
        <Text>
          {'  Battery  '}
          <Text color={mn > 3.0 ? 'green' : 'red'}>
            {mn.toFixed(2)}V--{mx.toFixed(2)}V
          </Text>
          {'  '}{mn > 3.0 ? 'OK' : 'LOW'}
        </Text>
      </Box>
    );
  }

  const simLabel = sim ? ' | OpenRocket sim loaded' : '';

  if (showLogViewer) {
    return (
      <Box flexDirection="column" width={DASH_WIDTH + 2}>
        <Header title="POST-FLIGHT ANALYSIS" width={DASH_WIDTH} />
        <LogViewer frames={frames} version={version} onClose={() => setShowLogViewer(false)} />
      </Box>
    );
  }

  return (
    <Box flexDirection="column" width={DASH_WIDTH + 2}>
      <Header title="POST-FLIGHT ANALYSIS" width={DASH_WIDTH} />
      <Text dimColor>  {basename(binFile)}  |  {frames.length.toLocaleString()} frames  |  log v{version}
        {skippedBytes > 0 ? `  |  ${skippedBytes} bytes skipped` : ''}{simLabel}
      </Text>
      <Text> </Text>

      {/* Flight summary — stage 1 */}
      {revealStage >= 1 && <FlightSummary stats={stats} />}

      {/* Altitude chart — stage 2, wave-in from bottom */}
      {revealStage >= 2 && (
        <AltitudeChart
          frames={frames}
          sim={sim}
          transitions={stats.transitions}
          revealSweep={chartSweep}
        />
      )}

      {/* State timeline — stage 3 */}
      {revealStage >= 3 && <StateTimeline stats={stats} frames={frames} />}

      {/* Velocity sparkline + power — stage 4 */}
      {revealStage >= 4 && (
        <>
          <Box flexDirection="column" marginBottom={1}>
            <Text bold>VELOCITY PROFILE</Text>
            <Text>
              {'  Vel  '}
              <Text color="cyan">{velSpark}</Text>
              {'  '}
              <Text color="cyan">{stats.maxVel > 0 ? '+' : ''}{stats.maxVel.toFixed(1)} m/s peak</Text>
            </Text>
          </Box>
          {powerContent}
        </>
      )}

      {/* Sim comparison + keys — stage 5 */}
      {revealStage >= 5 && (
        <>
          {sim && <SimCompare stats={stats} sim={sim} />}

          {stats.diag && (
            <DiagnosticsPanel diag={stats.diag} frames={frames} nFrames={stats.nFrames} />
          )}

          {error && <Text color="yellow">  {error}</Text>}

          {statusMsg && (
            <Text color={statusMsg.startsWith('\u2718') ? 'red' : 'green'}>
              {'  '}{exporting && <Spinner type="dots" />}{exporting && ' '}{statusMsg}
            </Text>
          )}

          <KeyBar
            keys={[
              ['F', 'Switch File'],
              ['L', 'Log Viewer'],
              ['D', 'Export to Desktop'],
              ['E', 'Export CSV'],
              ['S', 'Save Report'],
              ['Q', 'Quit'],
            ]}
            width={DASH_WIDTH}
          />
        </>
      )}

      {/* Show spinner while sections are still revealing */}
      {revealStage < 5 && revealStage >= 1 && (
        <Text color="yellow">  <Spinner type="dots" /> Loading dashboard...</Text>
      )}
    </Box>
  );
}
