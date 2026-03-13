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
function scanForFlightFolders(dir: string, volume: string): FoundFile[] {
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
            results.push({
              path: binPath,
              name: entry,           // folder name as display name
              size: fStat.size,
              mtime: fStat.mtime,
              volume,
              folder: entry,
              hasPreflight,
            });
          }
        }
      } catch { /* skip unreadable entries */ }
    }
  } catch { /* skip unreadable dir */ }
  return results;
}

/** Scan a directory for legacy flat .bin files */
function scanForFlatBinFiles(dir: string, volume: string): FoundFile[] {
  const results: FoundFile[] = [];
  try {
    const files = readdirSync(dir).filter(f => f.endsWith('.bin'));
    for (const f of files) {
      const fPath = join(dir, f);
      try {
        const fStat = statSync(fPath);
        if (fStat.size > 50) {
          results.push({ path: fPath, name: f, size: fStat.size, mtime: fStat.mtime, volume, folder: null, hasPreflight: false });
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

        // Scan for flight folders (new per-flight layout)
        results.push(...scanForFlightFolders(volPath, vol));

        // Scan root for legacy flat .bin files
        results.push(...scanForFlatBinFiles(volPath, vol));

        // Also check /sd subdirectory (some card readers mount the Pico's filesystem)
        const sdPath = join(volPath, 'sd');
        if (existsSync(sdPath)) {
          results.push(...scanForFlightFolders(sdPath, vol));
          results.push(...scanForFlatBinFiles(sdPath, vol));
        }
      } catch { /* skip inaccessible volumes */ }
    }
  } catch { /* /Volumes not readable */ }

  // Sort by folder name descending (newest/highest number first), then by mtime
  results.sort((a, b) => {
    // Folder-based flights sort by name (descending) so flight_002 > flight_001
    if (a.folder && b.folder) return b.folder.localeCompare(a.folder);
    // Folder-based flights come before legacy flat files
    if (a.folder && !b.folder) return -1;
    if (!a.folder && b.folder) return 1;
    // Legacy flat files sort by mtime
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

// ── Loading screen component ─────────────────────────────────────

type LoadPhase = 'scanning' | 'selecting' | 'copying' | 'parsing' | 'ready' | 'error';

interface FoundFile {
  path: string;
  name: string;
  size: number;
  mtime: Date;
  volume: string;
  folder: string | null;       // folder name (e.g. "flight_001") or null for legacy flat files
  hasPreflight: boolean;       // true if preflight.txt exists alongside the .bin
}

interface LoadStep {
  label: string;
  detail?: string;
  status: 'pending' | 'active' | 'done' | 'error';
}

function PostflightLoader({ onReady }: { onReady: (binPath: string) => void }) {
  const { exit } = useApp();
  const [phase, setPhase] = useState<LoadPhase>('scanning');
  const [files, setFiles] = useState<FoundFile[]>([]);
  const [selected, setSelected] = useState(0);
  const [steps, setSteps] = useState<LoadStep[]>([
    { label: 'Scanning mounted volumes', status: 'active' },
    { label: 'Locating flight logs', status: 'pending' },
    { label: 'Copying to local storage', status: 'pending' },
    { label: 'Locating simulation data', status: 'pending' },
    { label: 'Decoding flight frames', status: 'pending' },
  ]);
  const [errorMsg, setErrorMsg] = useState('');

  const updateStep = useCallback((index: number, updates: Partial<LoadStep>) => {
    setSteps(prev => prev.map((s, i) => i === index ? { ...s, ...updates } : s));
  }, []);

  const activateStep = useCallback((index: number) => {
    setSteps(prev => prev.map((s, i) => {
      if (i === index) return { ...s, status: 'active' };
      return s;
    }));
  }, []);

  // Scan on mount
  useEffect(() => {
    const timer = setTimeout(() => {
      const found = findBinFilesOnVolumes();
      updateStep(0, { status: 'done', detail: `/Volumes/ — ${found.length > 0 ? found[0].volume : 'no external volumes'}` });

      if (found.length === 0) {
        updateStep(1, { status: 'error', detail: 'No .bin flight logs found' });
        setPhase('error');
        setErrorMsg('Insert SD card and try again, or: pnpm dev:tui -- postflight <file.bin>');
        return;
      }

      setTimeout(() => {
        updateStep(1, {
          status: 'done',
          detail: `${found.length} log${found.length > 1 ? 's' : ''} on ${found[0].volume}`,
        });
        setFiles(found);
        setPhase('selecting');
      }, 500);
    }, 600);
    return () => clearTimeout(timer);
  }, []);

  const loadFile = useCallback((file: FoundFile) => {
    setPhase('copying');
    activateStep(2);

    setTimeout(() => {
      try {
        const flightsDir = getFlightsDir();
        if (!existsSync(flightsDir)) {
          mkdirSync(flightsDir, { recursive: true });
        }

        const destName = file.folder
          ? `${file.volume}_${file.folder}.bin`
          : `${file.volume}_${file.name}`;
        let destPath = join(flightsDir, destName);

        // Check if an identical file already exists (same size = same flight)
        let needsCopy = true;
        if (existsSync(destPath)) {
          const existingStat = statSync(destPath);
          if (existingStat.size === file.size) {
            needsCopy = false;
          } else {
            // Different file with same name — don't overwrite, create unique name
            destPath = uniquePath(destPath);
          }
        }

        if (needsCopy) {
          copyFileSync(file.path, destPath);
          updateStep(2, { status: 'done', detail: `flights/${basename(destPath)} (${formatSize(file.size)})` });
        } else {
          updateStep(2, { status: 'done', detail: `Already cached (${formatSize(file.size)})` });
        }

        // Check for sim data
        setTimeout(() => {
          activateStep(3);
          setTimeout(() => {
            // findSimFile is in use-flight-data, but we can check for sims/ dir
            const simsDir = resolve(process.cwd(), '../../sims');
            const hasSims = existsSync(simsDir);
            updateStep(3, {
              status: 'done',
              detail: hasSims ? 'OpenRocket sim found in sims/' : 'No sim data (optional)',
            });

            // Parse
            setTimeout(() => {
              activateStep(4);
              setPhase('parsing');
              setTimeout(() => {
                updateStep(4, { status: 'done', detail: `${file.name} ready` });
                setTimeout(() => onReady(destPath), 300);
              }, 400);
            }, 300);
          }, 400);
        }, 300);
      } catch (e) {
        updateStep(2, { status: 'error', detail: e instanceof Error ? e.message : String(e) });
        // Fall back to reading from SD directly
        activateStep(3);
        updateStep(3, { status: 'done', detail: 'Skipped' });
        activateStep(4);
        setPhase('parsing');
        setTimeout(() => {
          updateStep(4, { status: 'done', detail: 'Reading from SD card' });
          setTimeout(() => onReady(file.path), 300);
        }, 400);
      }
    }, 500);
  }, [activateStep, updateStep, onReady]);

  useInput((input, key) => {
    if (input === 'q' || input === 'Q') exit();

    if (phase === 'selecting') {
      if (key.upArrow && selected > 0) setSelected(s => s - 1);
      if (key.downArrow && selected < files.length - 1) setSelected(s => s + 1);
      if (key.return) {
        loadFile(files[selected]);
      }
    }
  });

  const stepIcon = (s: LoadStep) => {
    switch (s.status) {
      case 'done': return '\u2714';   // ✔
      case 'error': return '\u2718';  // ✘
      case 'active': return '\u25cb'; // ○ (spinner replaces this visually)
      default: return '\u2500';       // ─
    }
  };

  const stepColor = (s: LoadStep): string => {
    switch (s.status) {
      case 'done': return 'green';
      case 'error': return 'red';
      case 'active': return 'yellow';
      default: return 'gray';
    }
  };

  return (
    <Box flexDirection="column" width={DASH_WIDTH + 2}>
      <Header title="POST-FLIGHT ANALYSIS" width={DASH_WIDTH} />

      <Panel title="LOADING FLIGHT DATA" width={DASH_WIDTH} borderColor="cyan">
        <Text>{' '}</Text>
        {steps.map((step, i) => (
          <React.Fragment key={i}>
            <Text>
              {'  '}
              {step.status === 'active' ? (
                <Text color="yellow"><Spinner type="dots" /></Text>
              ) : (
                <Text color={stepColor(step)}>{stepIcon(step)}</Text>
              )}
              {'  '}
              <Text color={step.status === 'pending' ? 'gray' : 'white'} bold={step.status === 'active'}>
                {step.label}
              </Text>
              {step.detail && step.status !== 'pending' && (
                <Text dimColor>{'  — '}{step.detail}</Text>
              )}
            </Text>
          </React.Fragment>
        ))}
        <Text>{' '}</Text>

        {errorMsg && (
          <Text color="red">  {errorMsg}</Text>
        )}
      </Panel>

      {phase === 'selecting' && (
        <>
          <Text>{' '}</Text>
          <Panel title="SELECT FLIGHT LOG" width={DASH_WIDTH} borderColor="yellow">
            <Text dimColor> Use arrow keys to select, Enter to load:</Text>
            <Text>{' '}</Text>
            {files.map((f, i) => {
              const date = f.mtime.toLocaleDateString() + ' ' + f.mtime.toLocaleTimeString();
              const prefix = i === selected ? ' \u25b6 ' : '   ';
              const displayName = f.folder ?? f.name;
              const preflightTag = f.hasPreflight ? ' [preflight]' : '';
              return (
                <Text key={f.path} color={i === selected ? 'cyan' : undefined} bold={i === selected}>
                  {prefix}{(displayName + preflightTag).padEnd(30)}{formatSize(f.size).padEnd(12)}{date.padEnd(22)}{f.volume}
                </Text>
              );
            })}
          </Panel>
        </>
      )}

      <KeyBar
        keys={
          phase === 'selecting'
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

  // If a bin file was given directly, go straight to dashboard
  if (resolvedFile) {
    return <PostflightDashboard binFile={resolvedFile} simFile={simFile} />;
  }

  // Otherwise, show the auto-discovery loader
  return <PostflightLoader onReady={(path) => setResolvedFile(path)} />;
}

// ── Dashboard component ──────────────────────────────────────────

interface DashboardProps {
  binFile: string;
  simFile?: string;
}

function PostflightDashboard({ binFile, simFile }: DashboardProps) {
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
                const match = basename(binFile, '.bin').match(/^.+?_(.+)$/);
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
          {error && <Text color="yellow">  {error}</Text>}

          {statusMsg && (
            <Text color={statusMsg.startsWith('\u2718') ? 'red' : 'green'}>
              {'  '}{exporting && <Spinner type="dots" />}{exporting && ' '}{statusMsg}
            </Text>
          )}

          <KeyBar
            keys={[
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
