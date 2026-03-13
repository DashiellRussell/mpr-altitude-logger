import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Box, Text, useInput, useApp } from 'ink';
import Spinner from 'ink-spinner';
import { rawToVoltage, RAIL_SPECS, sparkline as makeSparkline } from '@mpr/shared';
import { usePico } from '../hooks/use-pico.js';
import { useTelemetry } from '../hooks/use-telemetry.js';
import { Header } from '../components/header.js';
import { Panel } from '../components/panel.js';
import { CheckItem } from '../components/check-item.js';
import { StatusDot } from '../components/status-dot.js';
import { GoNogo } from '../components/go-nogo.js';
import { VoltageBar } from '../components/voltage-bar.js';
import { KeyBar } from '../components/key-bar.js';
import { LedIndicator } from '../components/led-indicator.js';
import { SDCardScreen } from './sdcard.js';
import {
  SYSINFO_CODE,
  I2C_SCAN_CODE,
  BARO_CHECK_CODE,
  SD_CHECK_CODE,
  ADC_CHECK_CODE,
  LED_CHECK_CODE,
  WRITE_OVERRIDE_FLAG_CODE,
  I2C_DETAIL_CODE,
  BARO_DETAIL_CODE,
  SD_DETAIL_CODE,
  ADC_DETAIL_CODE,
  LED_DETAIL_CODE,
} from '../serial/commands.js';

const TUI_VERSION = '1.4.1';
const DASH_WIDTH = 120;
const LEFT_W = 58;
const RIGHT_W = 60;

interface Props {
  port?: string;
}

type Phase = 'connect' | 'checks' | 'live' | 'booting';
type CheckStatus = 'pending' | 'running' | 'pass' | 'fail' | 'skip';

interface BootStep {
  step: number;
  label: string;
  status: 'pending' | 'ok' | 'fail' | 'warn';
  detail: string;
}

interface Check {
  name: string;
  status: CheckStatus;
  detail: string;
}

export function Preflight({ port }: Props) {
  const { exit } = useApp();
  const pico = usePico(port);
  const [phase, setPhase] = useState<Phase>('connect');
  const [checks, setChecks] = useState<Check[]>([
    { name: 'I2C Bus', status: 'pending', detail: '' },
    { name: 'Barometer', status: 'pending', detail: '' },
    { name: 'SD Card', status: 'pending', detail: '' },
    { name: 'Voltages', status: 'pending', detail: '' },
    { name: 'LED', status: 'pending', detail: '' },
  ]);
  const [sysinfo, setSysinfo] = useState({ version: '', freq: '', mem: 0, avionicsVersion: '' });
  const [busy, setBusy] = useState('');
  const [issues, setIssues] = useState<string[]>([]);
  const [sdInfo, setSdInfo] = useState({ total: 0, free: 0 });
  const [manualGo, setManualGo] = useState(false);
  const [showDetail, setShowDetail] = useState(false);
  const [showSDCard, setShowSDCard] = useState(false);

  // ── Boot sequence state ─────────────────────────────────────
  const BOOT_LABELS = ['Overclock', 'LED started', 'SD card', 'Barometer', 'Power rails', 'Calibrating', 'Logger open'];
  const [bootSteps, setBootSteps] = useState<BootStep[]>([]);
  const [bootReady, setBootReady] = useState(false);
  const [bootError, setBootError] = useState<string | null>(null);
  const [padTelemetry, setPadTelemetry] = useState<string | null>(null);
  const [bootConfirm, setBootConfirm] = useState(false);
  const [bootCountdown, setBootCountdown] = useState<number | null>(null);
  const [bootDebugLines, setBootDebugLines] = useState<string[]>([]);
  const [debugTick, setDebugTick] = useState(0);
  const bootLineHandlerRef = useRef<((line: string) => void) | null>(null);

  // Sub-check results for the detail view
  interface SubCheck { name: string; status: 'PASS' | 'FAIL'; detail: string }
  interface DetailResult { subchecks: SubCheck[]; running: boolean; error?: string }
  const [detailResults, setDetailResults] = useState<Record<string, DetailResult>>({});
  const detailRunningRef = useRef(false);

  const telemetry = useTelemetry(pico.link, phase === 'live');
  const runningRef = useRef(false);

  // Store raw logs from each check for the detail view
  interface CheckLog { stdout: string; stderr: string; ts: number }
  const checkLogs = useRef<Record<string, CheckLog>>({});

  const logCheck = (name: string, stdout: string, stderr: string) => {
    checkLogs.current[name] = { stdout, stderr, ts: Date.now() };
  };

  // ── Boot sequence logic ──────────────────────────────────────
  const triggerBoot = useCallback(async () => {
    // Init 7 pending steps
    const steps: BootStep[] = BOOT_LABELS.map((label, i) => ({
      step: i + 1,
      label,
      status: 'pending' as const,
      detail: '',
    }));
    setBootSteps(steps);
    setBootReady(false);
    setBootError(null);
    setPadTelemetry(null);
    setBootCountdown(null);
    setPhase('booting');

    // Line parser for main.py boot output
    const handleBootLine = (line: string) => {
      // Debug: capture last few lines so we can see what's arriving
      setBootDebugLines((prev) => [...prev.slice(-29), line]);

      // Match [N/7] step lines
      const stepMatch = line.match(/^\[(\d)\/7\]\s+(.*)$/);
      if (stepMatch) {
        const num = parseInt(stepMatch[1]);
        const rest = stepMatch[2];
        const hasOk = /\bOK\b/.test(rest);
        const hasFail = /\bFAIL\b/.test(rest);
        const hasWarn = /\bWARN\b/.test(rest);
        const status = hasFail ? 'fail' as const : hasWarn ? 'warn' as const : hasOk || num <= 2 ? 'ok' as const : 'ok' as const;
        setBootSteps((prev) =>
          prev.map((s) => s.step === num ? { ...s, status, detail: rest.trim() } : s)
        );
        return;
      }

      // Match [RDY]
      if (line.includes('[RDY]')) {
        setBootReady(true);
        return;
      }

      // Match [FATAL]
      if (line.includes('[FATAL]') || line.includes('FATAL')) {
        setBootError(line);
        return;
      }

      // Match PREFLIGHT FAILED
      if (line.includes('PREFLIGHT FAILED')) {
        setBootError('Preflight failed on board — check serial output');
        return;
      }

      // Match countdown (N...)
      const countdownMatch = line.match(/^\s*(\d+)\.\.\.$/);
      if (countdownMatch) {
        setBootCountdown(parseInt(countdownMatch[1]));
        return;
      }

      // Match PAD telemetry line: [PAD     ] alt=...
      if (line.match(/^\[(?:PAD|BOOST|COAST|APOGEE|DROGUE|MAIN|LANDED)\s*\]/)) {
        setPadTelemetry(line.trim());
        return;
      }
    };

    // Register listener BEFORE reset so we don't miss early boot lines
    bootLineHandlerRef.current = handleBootLine;
    pico.onLine(handleBootLine);

    try {
      // Re-enter REPL if we're in passthrough mode (retry after failed boot)
      if (pico.mode === 'passthrough') {
        await pico.enterRepl();
      }
      // Write override flag file if manual GO is active
      if (manualGo) {
        await pico.execRaw(WRITE_OVERRIDE_FLAG_CODE, 5000);
      }
      await pico.softReset();
    } catch (e) {
      setBootError(`Reset failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, [pico, manualGo]);

  // Cleanup boot line listener on unmount only
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    return () => {
      if (bootLineHandlerRef.current) {
        pico.offLine(bootLineHandlerRef.current);
      }
    };
  }, []);

  // Debug ticker — force re-render every 500ms during boot to update debug counters
  useEffect(() => {
    if (phase !== 'booting') return;
    const interval = setInterval(() => setDebugTick((t) => t + 1), 500);
    return () => clearInterval(interval);
  }, [phase]);

  // Parse "SubName:PASS:detail" lines from detailed check output
  const parseDetailOutput = (stdout: string): SubCheck[] => {
    return stdout.trim().split('\n').filter(l => l.includes(':')).map(line => {
      const parts = line.split(':');
      const name = parts[0].trim();
      const status = parts[1]?.trim() === 'PASS' ? 'PASS' as const : 'FAIL' as const;
      const detail = parts.slice(2).join(':').trim();
      return { name, status, detail };
    });
  };

  // Run all detailed checks
  const runDetailedChecks = useCallback(async () => {
    if (!pico.connected || detailRunningRef.current) return;
    detailRunningRef.current = true;

    const categories: Array<[string, string]> = [
      ['I2C Bus', I2C_DETAIL_CODE],
      ['Barometer', BARO_DETAIL_CODE],
      ['SD Card', SD_DETAIL_CODE],
      ['Voltages', ADC_DETAIL_CODE],
      ['LED', LED_DETAIL_CODE],
    ];

    for (const [catName, code] of categories) {
      setDetailResults(prev => ({ ...prev, [catName]: { subchecks: [], running: true } }));
      await delay(500);
      try {
        const { stdout, stderr } = await pico.execRaw(code, 15000);
        if (stderr) {
          setDetailResults(prev => ({
            ...prev,
            [catName]: { subchecks: [{ name: 'Error', status: 'FAIL', detail: stderr }], running: false, error: stderr },
          }));
        } else {
          const subchecks = parseDetailOutput(stdout);
          setDetailResults(prev => ({ ...prev, [catName]: { subchecks, running: false } }));
        }
      } catch (e) {
        setDetailResults(prev => ({
          ...prev,
          [catName]: { subchecks: [{ name: 'Exception', status: 'FAIL', detail: String(e) }], running: false, error: String(e) },
        }));
      }
    }

    detailRunningRef.current = false;
  }, [pico.connected, pico.execRaw]);

  const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

  const updateCheck = useCallback(
    (name: string, status: CheckStatus, detail: string) => {
      setChecks((prev) =>
        prev.map((c) => (c.name === name ? { ...c, status, detail } : c))
      );
    },
    []
  );

  // ── System info ──────────────────────────────────────────────────
  const readSysinfo = useCallback(async () => {
    if (!pico.connected) return;
    setBusy('Reading system info...');
    try {
      const { stdout, stderr } = await pico.execRaw(SYSINFO_CODE, 5000);
      logCheck('System Info', stdout, stderr);
      if (!stderr) {
        const parts = stdout.trim().split(',');
        const ver = parts[0]?.trim() ?? '?';
        const freq = parts[1] ? `${Math.floor(parseInt(parts[1]) / 1_000_000)} MHz` : '?';
        const mem = parts[2] ? parseInt(parts[2]) : 0;
        const avVer = parts[3]?.trim() ?? '?';
        setSysinfo({
          version: ver.length > 35 ? ver.slice(0, 35) + '...' : ver,
          freq,
          mem,
          avionicsVersion: avVer,
        });
      }
    } catch (e) {
      setIssues((prev) => [...prev, `System info: ${e}`]);
    }
    setBusy('');
  }, [pico.connected, pico.execRaw]);

  // ── Individual checks ────────────────────────────────────────────
  const checkI2C = useCallback(async () => {
    updateCheck('I2C Bus', 'running', '');
    await delay(1000);
    try {
      const { stdout, stderr } = await pico.execRaw(I2C_SCAN_CODE, 5000);
      logCheck('I2C Bus', stdout, stderr);
      if (stderr) {
        updateCheck('I2C Bus', 'fail', stderr);
        setIssues((prev) => [...prev, 'I2C scan failed']);
        return;
      }
      const addrs = stdout.trim().split(',').filter((x) => x.trim()).map((x) => parseInt(x));
      if (addrs.includes(0x77)) {
        const hexList = addrs.map((a) => `0x${a.toString(16).toUpperCase().padStart(2, '0')}`).join(', ');
        updateCheck('I2C Bus', 'pass', `Devices: ${hexList}`);
      } else {
        updateCheck('I2C Bus', 'fail', `0x77 not found. Got: [${addrs.map(a => '0x'+a.toString(16)).join(', ')}]`);
        setIssues((prev) => [...prev, 'BMP180 not found on I2C']);
      }
    } catch (e) {
      updateCheck('I2C Bus', 'fail', String(e));
      setIssues((prev) => [...prev, 'I2C scan error']);
    }
  }, [pico.execRaw, updateCheck]);

  const checkBarometer = useCallback(async () => {
    updateCheck('Barometer', 'running', '');
    await delay(1000);
    try {
      const { stdout, stderr } = await pico.execRaw(BARO_CHECK_CODE, 5000);
      logCheck('Barometer', stdout, stderr);
      if (stderr) {
        updateCheck('Barometer', 'fail', stderr);
        setIssues((prev) => [...prev, 'Barometer chip ID read failed']);
        return;
      }
      const chipId = parseInt(stdout.trim());
      if (chipId === 0x55) {
        updateCheck('Barometer', 'pass', `BMP180 chip ID 0x${chipId.toString(16).toUpperCase()}`);
      } else {
        updateCheck('Barometer', 'fail', `Got 0x${chipId.toString(16).toUpperCase()}, expected 0x55`);
        setIssues((prev) => [...prev, `Barometer chip ID mismatch`]);
      }
    } catch (e) {
      updateCheck('Barometer', 'fail', String(e));
      setIssues((prev) => [...prev, 'Barometer check error']);
    }
  }, [pico.execRaw, updateCheck]);

  const checkSD = useCallback(async () => {
    updateCheck('SD Card', 'running', '');
    await delay(1000);
    try {
      const { stdout, stderr } = await pico.execRaw(SD_CHECK_CODE, 10000);
      logCheck('SD Card', stdout, stderr);
      if (stderr) {
        updateCheck('SD Card', 'fail', stderr);
        setIssues((prev) => [...prev, 'SD card check failed']);
        return;
      }
      const parts = stdout.trim().split(',');
      const total = parseInt(parts[0]);
      const free = parseInt(parts[1]);
      const writeOk = parts[2]?.trim() === 'True';
      setSdInfo({ total, free });

      if (writeOk && free > 10) {
        updateCheck('SD Card', 'pass', `${total} MB total, ${free} MB free, write OK`);
      } else if (!writeOk) {
        updateCheck('SD Card', 'fail', 'Write/read verification failed');
        setIssues((prev) => [...prev, 'SD card write test failed']);
      } else {
        updateCheck('SD Card', 'fail', `Low space: ${free} MB free`);
        setIssues((prev) => [...prev, `SD card low space (${free} MB)`]);
      }
    } catch (e) {
      updateCheck('SD Card', 'fail', String(e));
      setIssues((prev) => [...prev, 'SD card not accessible']);
    }
  }, [pico.execRaw, updateCheck]);

  const checkADC = useCallback(async () => {
    updateCheck('Voltages', 'running', '');
    await delay(1000);
    try {
      const { stdout, stderr } = await pico.execRaw(ADC_CHECK_CODE, 5000);
      logCheck('Voltages', stdout, stderr);
      if (stderr) {
        updateCheck('Voltages', 'fail', stderr);
        setIssues((prev) => [...prev, 'ADC read failed']);
        return;
      }
      const parts = stdout.trim().split(',');
      const v3 = rawToVoltage(parseInt(parts[0]), RAIL_SPECS['3V3'].divider);
      const v5 = rawToVoltage(parseInt(parts[1]), RAIL_SPECS['5V'].divider);
      const v9 = rawToVoltage(parseInt(parts[2]), RAIL_SPECS['9V'].divider);

      const problems: string[] = [];
      if (v3 < RAIL_SPECS['3V3'].min || v3 > RAIL_SPECS['3V3'].max) problems.push(`3V3=${v3.toFixed(2)}V`);
      if (v5 < RAIL_SPECS['5V'].min || v5 > RAIL_SPECS['5V'].max) problems.push(`5V=${v5.toFixed(2)}V`);
      if (v9 < RAIL_SPECS['9V'].min || v9 > RAIL_SPECS['9V'].max) problems.push(`9V=${v9.toFixed(2)}V`);

      if (problems.length === 0) {
        updateCheck('Voltages', 'pass', `3V3=${v3.toFixed(2)}V  5V=${v5.toFixed(2)}V  9V=${v9.toFixed(2)}V`);
      } else {
        updateCheck('Voltages', 'fail', `Out of range: ${problems.join(', ')}`);
        setIssues((prev) => [...prev, `Voltage out of spec: ${problems.join(', ')}`]);
      }
    } catch (e) {
      updateCheck('Voltages', 'fail', String(e));
      setIssues((prev) => [...prev, 'ADC check error']);
    }
  }, [pico.execRaw, updateCheck]);

  const checkLED = useCallback(async () => {
    updateCheck('LED', 'running', '');
    await delay(1000);
    try {
      const { stdout, stderr } = await pico.execRaw(LED_CHECK_CODE, 5000);
      logCheck('LED', stdout, stderr);
      if (stderr) {
        updateCheck('LED', 'fail', stderr);
        setIssues((prev) => [...prev, 'LED test failed']);
        return;
      }
      const val = stdout.trim();
      if (val === 'OK') {
        updateCheck('LED', 'pass', 'Blinked OK — check board visually');
      } else {
        updateCheck('LED', 'fail', `Unexpected: ${val}`);
        setIssues((prev) => [...prev, 'LED test error']);
      }
    } catch (e) {
      updateCheck('LED', 'fail', String(e));
      setIssues((prev) => [...prev, 'LED test error']);
    }
  }, [pico.execRaw, updateCheck]);

  // ── Run all checks ───────────────────────────────────────────────
  const runChecks = useCallback(async () => {
    if (runningRef.current) return;
    runningRef.current = true;
    setChecks((prev) => prev.map((c) => ({ ...c, status: 'pending' as CheckStatus, detail: '' })));
    setIssues([]);

    await checkI2C();
    await checkBarometer();
    await checkSD();
    await checkADC();
    await checkLED();

    setPhase('live');
    runningRef.current = false;
  }, [checkI2C, checkBarometer, checkSD, checkADC, checkLED]);

  // ── Auto-run on connection ───────────────────────────────────────
  useEffect(() => {
    if (pico.connected && phase === 'connect') {
      (async () => {
        await readSysinfo();
        setPhase('checks');
        await runChecks();
      })();
    }
  }, [pico.connected, phase, readSysinfo, runChecks]);

  // ── Version mismatch detection ─────────────────────────────────
  const versionMismatch = sysinfo.avionicsVersion !== '' && sysinfo.avionicsVersion !== '?' && sysinfo.avionicsVersion !== TUI_VERSION;

  // ── GO status (computed early so key handler can use it) ────────
  const allPassed = checks.every((c) => c.status === 'pass' || c.status === 'skip');
  const isNaturalGo = allPassed && telemetry.voltagesOk && telemetry.baroSane && sdInfo.free > 10 && issues.length === 0 && !versionMismatch;
  const isGo = isNaturalGo || manualGo;

  // ── Key handling ─────────────────────────────────────────────────
  useInput((input, _key) => {
    // Disable when sub-screens are active
    if (showSDCard || showDetail) return;

    if (input === 'q' || input === 'Q') exit();

    // Boot phase: retry or go back to preflight
    if (phase === 'booting') {
      if (input === 'r' || input === 'R') {
        // Clean up old listener and retry boot
        if (bootLineHandlerRef.current) {
          pico.offLine(bootLineHandlerRef.current);
          bootLineHandlerRef.current = null;
        }
        setBootDebugLines([]);
        triggerBoot();
      }
      if (_key.escape || input === 'b' || input === 'B') {
        // Go back to preflight dashboard — re-enter REPL so checks work
        if (bootLineHandlerRef.current) {
          pico.offLine(bootLineHandlerRef.current);
          bootLineHandlerRef.current = null;
        }
        setBootDebugLines([]);
        setBootSteps([]);
        setBootError(null);
        setBootReady(false);
        pico.enterRepl().then(() => {
          setPhase('live');
        }).catch(() => {
          // If re-entering REPL fails, go back anyway
          setPhase('live');
        });
      }
      return;
    }

    // Boot sequence: requires GO (natural or manual override), two-press confirmation
    if ((input === 'b' || input === 'B') && phase === 'live') {
      if (!isGo) return; // silently ignore if NO-GO
      if (bootConfirm) {
        setBootConfirm(false);
        triggerBoot();
      } else {
        setBootConfirm(true);
      }
      return;
    }
    // Any other key cancels boot confirmation
    if (bootConfirm && phase === 'live') {
      setBootConfirm(false);
    }

    if ((input === 'r' || input === 'R') && showDetail && pico.connected) {
      setDetailResults({});
      runDetailedChecks();
      return;
    }
    if ((input === 'r' || input === 'R') && phase === 'live') telemetry.recalibrate();
    if ((input === 't' || input === 'T') && phase === 'live') {
      setManualGo(false);
      runChecks();
    }
    if ((input === 'g' || input === 'G') && phase === 'live') {
      setManualGo((prev) => !prev);
    }
    if ((input === 'd' || input === 'D') && phase !== 'booting') {
      setShowDetail((prev) => !prev);
    }
    if ((input === 's' || input === 'S') && phase === 'live' && pico.connected) {
      setShowSDCard(true);
    }
  });

  // ── Count stats ──────────────────────────────────────────────────
  const nPass = checks.filter((c) => c.status === 'pass').length;
  const nFail = checks.filter((c) => c.status === 'fail').length;
  const nSkip = checks.filter((c) => c.status === 'skip').length;
  const nTotal = checks.length;

  // velHistory is now tracked in useTelemetry hook

  // ── SD Card screen ─────────────────────────────────────────
  if (showSDCard) {
    return (
      <SDCardScreen
        pico={pico}
        onBack={() => setShowSDCard(false)}
      />
    );
  }

  // ── Detail view: sub-checks per category ─────────────────────
  const detailCategories = ['I2C Bus', 'Barometer', 'SD Card', 'Voltages', 'LED'];
  const NCOLS = detailCategories.length;
  const COL_W = Math.floor((DASH_WIDTH - (NCOLS - 1)) / NCOLS);

  if (showDetail) {
    // Auto-run detailed checks on first open if not already done
    const hasResults = detailCategories.some(n => detailResults[n]);
    if (!hasResults && pico.connected && !detailRunningRef.current) {
      runDetailedChecks();
    }

    // Count totals
    let totalSub = 0, passedSub = 0, failedSub = 0;
    for (const cat of detailCategories) {
      const r = detailResults[cat];
      if (r) {
        totalSub += r.subchecks.length;
        passedSub += r.subchecks.filter(s => s.status === 'PASS').length;
        failedSub += r.subchecks.filter(s => s.status === 'FAIL').length;
      }
    }

    return (
      <Box flexDirection="column" width={DASH_WIDTH + 2}>
        <Header title="DETAILED HARDWARE CHECK" width={DASH_WIDTH} />

        <Box flexDirection="row">
          {detailCategories.map((catName, i) => {
            const r = detailResults[catName];
            const check = checks.find(c => c.name === catName);
            const hasFail = r?.subchecks.some(s => s.status === 'FAIL');
            const allPass = r && !r.running && r.subchecks.length > 0 && !hasFail;
            const borderCol = r?.running ? 'yellow' : allPass ? 'green' : hasFail ? 'red' : 'blue';

            return (
              <React.Fragment key={catName}>
                {i > 0 && <Box width={1}><Text>{' '}</Text></Box>}
                <Box flexDirection="column" width={COL_W}>
                  <Panel title={catName} width={COL_W} borderColor={borderCol}>
                    {/* Quick check result */}
                    {check && (
                      <Text color={check.status === 'pass' ? 'green' : check.status === 'fail' ? 'red' : 'yellow'} bold>
                        {' '}{check.status === 'pass' ? 'PASS' : check.status === 'fail' ? 'FAIL' : check.status === 'skip' ? 'SKIP' : check.status === 'running' ? 'RUNNING' : 'PENDING'}
                      </Text>
                    )}
                    <Text>{' '}</Text>
                    {/* Sub-checks */}
                    {r?.running && (
                      <Text color="yellow"> <Spinner type="dots" /> Running sub-checks...</Text>
                    )}
                    {r?.subchecks.map((sc, j) => {
                      const maxD = COL_W - 14;
                      const dTrunc = sc.detail.length > maxD ? sc.detail.slice(0, maxD - 1) + '\u2026' : sc.detail;
                      return (
                        <React.Fragment key={j}>
                          <Text>
                            {' '}
                            {sc.status === 'PASS'
                              ? <Text color="green" bold>{'PASS'}</Text>
                              : <Text color="red" bold>{'FAIL'}</Text>
                            }
                            {'  '}
                            <Text>{sc.name}</Text>
                          </Text>
                          <Text dimColor>{'       '}{dTrunc}</Text>
                        </React.Fragment>
                      );
                    })}
                    {!r && !detailRunningRef.current && (
                      <Text dimColor> Not yet run</Text>
                    )}
                  </Panel>
                </Box>
              </React.Fragment>
            );
          })}
        </Box>

        {totalSub > 0 && (
          <Text>
            {'  '}{passedSub}/{totalSub} sub-checks passed
            {failedSub > 0 && <Text color="red" bold>{'  '}{failedSub} failed</Text>}
          </Text>
        )}

        <KeyBar keys={[['D', 'Back to Dashboard'], ['R', 'Re-run Detailed'], ['Q', 'Quit']]} width={DASH_WIDTH} />
      </Box>
    );
  }

  // ── Booting phase view ──────────────────────────────────────
  if (phase === 'booting') {
    const ledState = bootError ? 'solid-error' as const
      : bootReady ? 'solid-ready' as const
      : 'blink' as const;

    return (
      <Box flexDirection="column" width={DASH_WIDTH + 2}>
        <Header title="BOOT SEQUENCE" width={DASH_WIDTH} />

        {/* Status banner */}
        {bootError && (
          <Box flexDirection="column">
            <Text backgroundColor="red" color="black" bold>
              {'  \u2717  BOOT ERROR'.padEnd(DASH_WIDTH)}
            </Text>
            <Text>{' '}</Text>
          </Box>
        )}

        {bootReady && (
          <Box flexDirection="column">
            <Text backgroundColor="green" color="black" bold>
              {'  \u2605  READY FOR FLIGHT — Board logging at 25Hz. Safe to disconnect USB.'.padEnd(DASH_WIDTH)}
            </Text>
            <Text>{' '}</Text>
          </Box>
        )}

        {!bootReady && !bootError && (
          <Text color="yellow"> <Spinner type="dots" /> Pico is rebooting into flight mode...</Text>
        )}

        {bootCountdown !== null && !bootReady && !bootError && (
          <Text color="yellow"> Proceeding despite warnings in {bootCountdown}s...</Text>
        )}

        <Text>{' '}</Text>

        {/* Full-width serial log */}
        <Panel title="SERIAL OUTPUT" width={DASH_WIDTH} borderColor={bootError ? 'red' : bootReady ? 'green' : 'yellow'}>
          {bootDebugLines.length === 0 && (
            <Text dimColor> Waiting for output...</Text>
          )}
          {bootDebugLines.map((line, i) => {
            const isFatal = line.includes('FATAL') || line.includes('FAIL');
            const isOk = line.includes('OK') || line.includes('[RDY]');
            const isWarn = line.includes('WARN') || line.includes('retry');
            const color = isFatal ? 'red' : isOk ? 'green' : isWarn ? 'yellow' : undefined;
            return (
              <Text key={i} color={color}>
                {' '}{line}
              </Text>
            );
          })}
        </Panel>

        {/* PAD telemetry when ready */}
        {bootReady && padTelemetry && (
          <>
            <Text>{' '}</Text>
            <Panel title="PAD TELEMETRY (1 Hz)" width={DASH_WIDTH} borderColor="green">
              <Text> {padTelemetry}</Text>
            </Panel>
          </>
        )}

        <KeyBar keys={[['R', 'Retry Boot'], ['B', 'Back'], ['Q', 'Quit']]} width={DASH_WIDTH} />
      </Box>
    );
  }

  return (
    <Box flexDirection="column" width={DASH_WIDTH + 2}>
      <Header title="PRE-FLIGHT CHECK" width={DASH_WIDTH} />

      {/* ══════════ TWO-COLUMN LAYOUT ══════════ */}
      <Box flexDirection="row">

        {/* ──── LEFT COLUMN: System + Hardware Checks ──── */}
        <Box flexDirection="column" width={LEFT_W}>

          {/* System Info Panel */}
          <Panel title="SYSTEM" width={LEFT_W} borderColor="blue">
            <StatusDot connected={pico.connected} port={pico.portPath} error={pico.error} />
            {sysinfo.version ? (
              <Text> Firmware  {sysinfo.version}</Text>
            ) : (
              <Text dimColor> Firmware  --</Text>
            )}
            {sysinfo.avionicsVersion ? (
              <Text> Avionics  <Text bold>v{sysinfo.avionicsVersion}</Text>{'    '}TUI  <Text bold>v{TUI_VERSION}</Text>
                {sysinfo.avionicsVersion !== TUI_VERSION && sysinfo.avionicsVersion !== '?' && (
                  <Text color="red" bold>  VERSION MISMATCH</Text>
                )}
              </Text>
            ) : (
              <Text dimColor> Avionics  --</Text>
            )}
            {sysinfo.freq ? (
              <Text> CPU       {sysinfo.freq}{'    '}Mem  {sysinfo.mem > 0 ? `${(sysinfo.mem / 1024).toFixed(0)} KB free` : '--'}</Text>
            ) : (
              <Text dimColor> CPU       --</Text>
            )}
          </Panel>

          <Text>{' '}</Text>

          {/* Hardware Checks Panel */}
          <Panel title={`HARDWARE CHECKS  ${nPass}/${nTotal} passed${nFail > 0 ? `  ${nFail} failed` : ''}`} width={LEFT_W} borderColor={nFail > 0 ? 'red' : nPass === nTotal ? 'green' : 'yellow'}>
            {checks.map((c) => (
              <CheckItem key={c.name} {...c} maxWidth={LEFT_W - 2} />
            ))}
            <Text>{' '}</Text>
            <Text dimColor> {nPass > 0 ? `\u2713 ${nPass} pass` : ''}{nFail > 0 ? `  \u2717 ${nFail} fail` : ''}{nSkip > 0 ? `  \u2192 ${nSkip} skip` : ''}{phase === 'checks' ? '  Running...' : ''}</Text>
          </Panel>

        </Box>

        {/* ──── 2-char gap ──── */}
        <Box width={2}><Text>{'  '}</Text></Box>

        {/* ──── RIGHT COLUMN: Live Telemetry + Power ──── */}
        <Box flexDirection="column" width={RIGHT_W}>

          {phase === 'live' && telemetry.active ? (
            <>
              {/* Barometer / Altitude Panel */}
              <Panel title="BAROMETER" width={RIGHT_W} borderColor="cyan">
                <Text>
                  {' Pressure  '}
                  <Text bold>{telemetry.pressure.toFixed(0).padStart(7)}</Text>
                  {' Pa'}
                </Text>
                <Text>
                  {' Temp      '}
                  <Text bold>{telemetry.temp.toFixed(1).padStart(7)}</Text>
                  {' \u00b0C'}
                </Text>
                <Text>
                  {' Ground    '}
                  <Text dimColor>{telemetry.groundPa.toFixed(0).padStart(7)} Pa</Text>
                </Text>
              </Panel>

              <Text>{' '}</Text>

              {/* Altitude + Velocity Panel */}
              <Panel title="ALTITUDE & VELOCITY" width={RIGHT_W} borderColor="cyan">
                <Text>
                  {' Altitude'.padEnd(30)}{'Velocity'}
                </Text>
                <Text>
                  {' '}<Text bold color="cyan">{telemetry.alt.toFixed(1).padStart(8)} m AGL</Text>
                  {''.padEnd(18)}
                  <Text bold color={telemetry.velocity > 1 ? 'green' : telemetry.velocity < -1 ? 'red' : 'white'}>
                    {(telemetry.velocity > 0 ? '+' : '')}{telemetry.velocity.toFixed(1).padStart(7)} m/s
                  </Text>
                </Text>
                <Text>{' '}</Text>
                <Text>
                  {' Alt '}
                  <Text color="cyan">{makeSparkline(telemetry.altHistory, 44)}</Text>
                  {' '}{telemetry.alt.toFixed(1)}m
                </Text>
                <Text>
                  {' Vel '}
                  <Text color="green">{makeSparkline(telemetry.velHistory, 44)}</Text>
                  {' '}{telemetry.velocity.toFixed(1)}m/s
                </Text>
              </Panel>

              <Text>{' '}</Text>

              {/* Power Rails Panel */}
              <Panel title="POWER RAILS" width={RIGHT_W} borderColor={telemetry.voltagesOk ? 'green' : 'red'}>
                <VoltageBar label="3V3" value={telemetry.v3} rail="3V3" barWidth={20} />
                <VoltageBar label="5V " value={telemetry.v5} rail="5V" barWidth={20} />
                <VoltageBar label="9V " value={telemetry.v9} rail="9V" barWidth={20} />
              </Panel>

              <Text>{' '}</Text>

              {/* Sample counter */}
              <Text dimColor>
                {' '}2 Hz {'\u2022'} {telemetry.samples} samples
              </Text>
            </>
          ) : (
            /* Waiting state for right column */
            <Panel title="LIVE TELEMETRY" width={RIGHT_W} borderColor="dim">
              <Text>{' '}</Text>
              {phase === 'connect' && !pico.connected && (
                <Text color="yellow">
                  {' '}<Spinner type="dots" /> Searching for Pico...
                </Text>
              )}
              {phase === 'connect' && pico.connected && (
                <Text color="yellow">
                  {' '}<Spinner type="dots" /> Connected, reading system info...
                </Text>
              )}
              {phase === 'checks' && (
                <Text color="yellow">
                  {' '}<Spinner type="dots" /> Running hardware checks...
                </Text>
              )}
              {phase === 'live' && !telemetry.active && (
                <Text color="yellow">
                  {' '}<Spinner type="dots" /> Initialising sensors...
                </Text>
              )}
              <Text>{' '}</Text>
              <Text dimColor> Telemetry will appear here once</Text>
              <Text dimColor> hardware checks complete and</Text>
              <Text dimColor> sensors are initialised.</Text>
              <Text>{' '}</Text>
            </Panel>
          )}

        </Box>
      </Box>

      {/* ══════════ FULL-WIDTH BOTTOM: GO/NO-GO + Keys ══════════ */}
      <Text>{' '}</Text>

      {phase === 'live' && (
        <GoNogo
          checks={checks}
          voltagesOk={telemetry.voltagesOk}
          baroSane={telemetry.baroSane}
          sdFree={sdInfo.free}
          issues={issues}
          width={DASH_WIDTH}
          manualOverride={manualGo}
        />
      )}

      {busy && (
        <Text color="yellow">
          {'  '}<Spinner type="dots" /> {busy}
        </Text>
      )}

      {bootConfirm && phase === 'live' && (
        <Text backgroundColor="yellowBright" color="black" bold>
          {'  Press [B] again to start boot sequence. Any other key to cancel.'.padEnd(DASH_WIDTH)}
        </Text>
      )}

      <KeyBar
        keys={
          phase === 'live'
            ? [
                ...(isGo ? [['B', 'Boot Sequence'] as [string, string]] : []),
                ['R', 'Recalibrate'],
                ['T', 'Re-test'],
                ['S', 'SD Card'],
                ['G', manualGo ? 'Remove Override' : 'Manual GO Override'],
                ['D', 'Detail Logs'],
                ['Q', 'Quit'],
              ]
            : [['D', 'Detail Logs'], ['Q', 'Quit']]
        }
        width={DASH_WIDTH}
      />
    </Box>
  );
}
