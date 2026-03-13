import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Box, Text, useInput } from 'ink';
import Spinner from 'ink-spinner';
import { Header } from '../components/header.js';
import { Panel } from '../components/panel.js';
import { KeyBar } from '../components/key-bar.js';
import {
  SD_LIST_CODE,
  SD_WIPE_CODE,
  SD_SET_NAME_CODE,
  SD_CLEAR_NAME_CODE,
} from '../serial/commands.js';

const DASH_WIDTH = 120;
const LEFT_W = 72;
const RIGHT_W = 46;

interface SDFlight {
  name: string;
  totalSize: number;
  files: { name: string; size: number }[];
  hasPreflight: boolean;
  hasBin: boolean;
}

interface SDFile {
  name: string;
  size: number;
}

interface SDCardScreenProps {
  pico: {
    connected: boolean;
    execRaw: (code: string, timeout?: number) => Promise<{ stdout: string; stderr: string }>;
  };
  onBack: () => void;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function SDCardScreen({ pico, onBack }: SDCardScreenProps) {
  const [flights, setFlights] = useState<SDFlight[]>([]);
  const [rootFiles, setRootFiles] = useState<SDFile[]>([]);
  const [total, setTotal] = useState(0);
  const [free, setFree] = useState(0);
  const [nextFile, setNextFile] = useState('');
  const [nameOverride, setNameOverride] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Wipe state
  const [wipeConfirm, setWipeConfirm] = useState(false);
  const [wipeStatus, setWipeStatus] = useState<'idle' | 'wiping' | 'done' | 'error'>('idle');
  const [wipeMessage, setWipeMessage] = useState('');

  // Name input state
  const [naming, setNaming] = useState(false);
  const [nameInput, setNameInput] = useState('');

  const loadingRef = useRef(false);

  const loadFiles = useCallback(async () => {
    if (!pico.connected || loadingRef.current) return;
    loadingRef.current = true;
    setLoading(true);
    setError(null);
    setFlights([]);
    setRootFiles([]);
    setNameOverride(null);

    try {
      // Retry up to 3 times — raw REPL sometimes fails on first attempt
      let stdout = '';
      let lastErr = '';
      for (let attempt = 0; attempt < 3; attempt++) {
        const result = await pico.execRaw(SD_LIST_CODE, 15000);
        if (!result.stderr && result.stdout.includes('CAP:')) {
          stdout = result.stdout;
          lastErr = '';
          break;
        }
        lastErr = result.stderr || 'No valid response';
        if (attempt < 2) await new Promise((r) => setTimeout(r, 500));
      }
      if (lastErr) {
        setError(lastErr);
        setLoading(false);
        loadingRef.current = false;
        return;
      }

      const lines = stdout.trim().split('\n');
      const flightMap = new Map<string, SDFlight>();
      const files: SDFile[] = [];

      for (const line of lines) {
        if (line.startsWith('CAP:')) {
          const parts = line.slice(4).split(',');
          setTotal(parseInt(parts[0]) || 0);
          setFree(parseInt(parts[1]) || 0);
        } else if (line.startsWith('DIR:')) {
          const parts = line.slice(4).split(':');
          const name = parts[0];
          const totalSize = parseInt(parts[1]) || 0;
          flightMap.set(name, { name, totalSize, files: [], hasPreflight: false, hasBin: false });
        } else if (line.startsWith('DIRFILE:')) {
          const parts = line.slice(8).split(':');
          const dirName = parts[0];
          const fileName = parts[1];
          const size = parseInt(parts[2]) || 0;
          const flight = flightMap.get(dirName);
          if (flight) {
            flight.files.push({ name: fileName, size });
            if (fileName === 'preflight.txt') flight.hasPreflight = true;
            if (fileName === 'flight.bin') flight.hasBin = true;
          }
        } else if (line.startsWith('FILE:')) {
          const parts = line.slice(5).split(':');
          files.push({ name: parts[0], size: parseInt(parts[1]) || 0 });
        } else if (line.startsWith('NEXT:')) {
          setNextFile(line.slice(5));
        } else if (line.startsWith('OVERRIDE:')) {
          setNameOverride(line.slice(9));
        }
      }

      setFlights(Array.from(flightMap.values()));
      setRootFiles(files.filter((f) => !f.name.startsWith('_') || f.name === '_flight_name.txt'));
    } catch (e) {
      setError(String(e));
    }

    setLoading(false);
    loadingRef.current = false;
  }, [pico.connected, pico.execRaw]);

  useEffect(() => {
    loadFiles();
  }, [loadFiles]);

  const wipeSD = useCallback(async () => {
    if (!pico.connected) return;
    setWipeStatus('wiping');
    setWipeMessage('Wiping flight data...');
    try {
      const { stdout, stderr } = await pico.execRaw(SD_WIPE_CODE, 30000);
      if (stderr) {
        setWipeStatus('error');
        setWipeMessage(`Wipe failed: ${stderr}`);
      } else {
        const wipeLine = stdout.trim().split('\n').find((l) => l.startsWith('WIPE:'));
        if (wipeLine) {
          const parts = wipeLine.slice(5).split(',');
          const removed = parseInt(parts[0]) || 0;
          const newFree = parseInt(parts[1]) || 0;
          setWipeStatus('done');
          setWipeMessage(`Removed ${removed} flight(s). ${newFree} MB free.`);
        } else {
          setWipeStatus('done');
          setWipeMessage('Wipe completed.');
        }
        await loadFiles();
      }
    } catch (e) {
      setWipeStatus('error');
      setWipeMessage(`Wipe error: ${e}`);
    }
  }, [pico.connected, pico.execRaw, loadFiles]);

  const setFlightName = useCallback(async (name: string) => {
    if (!pico.connected) return;
    try {
      const { stderr } = await pico.execRaw(SD_SET_NAME_CODE(name), 10000);
      if (stderr) {
        setError(`Failed to set name: ${stderr}`);
      } else {
        await loadFiles();
      }
    } catch (e) {
      setError(`Name set error: ${e}`);
    }
  }, [pico.connected, pico.execRaw, loadFiles]);

  const clearFlightName = useCallback(async () => {
    if (!pico.connected) return;
    try {
      const { stderr } = await pico.execRaw(SD_CLEAR_NAME_CODE, 10000);
      if (stderr) {
        setError(`Failed to clear name: ${stderr}`);
      } else {
        await loadFiles();
      }
    } catch (e) {
      setError(`Name clear error: ${e}`);
    }
  }, [pico.connected, pico.execRaw, loadFiles]);

  useInput((input, key) => {
    // Name input mode
    if (naming) {
      if (key.return) {
        const trimmed = nameInput.trim();
        if (trimmed) {
          setFlightName(trimmed);
        }
        setNaming(false);
        setNameInput('');
        return;
      }
      if (key.escape) {
        setNaming(false);
        setNameInput('');
        return;
      }
      if (key.backspace || key.delete) {
        setNameInput((prev) => prev.slice(0, -1));
        return;
      }
      if (input && /^[a-zA-Z0-9_\-]$/.test(input)) {
        setNameInput((prev) => prev + input);
      }
      return;
    }

    if (input === 's' || input === 'S' || key.escape) {
      onBack();
      return;
    }

    if (input === 'q' || input === 'Q') {
      onBack();
      return;
    }

    // Wipe: two-press confirmation
    if (input === 'w' || input === 'W') {
      if (wipeConfirm) {
        setWipeConfirm(false);
        wipeSD();
      } else {
        setWipeConfirm(true);
        setWipeStatus('idle');
        setWipeMessage('');
      }
      return;
    }

    if (input === 'r' || input === 'R') {
      loadFiles();
      return;
    }

    if (input === 'n' || input === 'N') {
      setNaming(true);
      setNameInput('');
      return;
    }

    if (input === 'c' || input === 'C') {
      if (nameOverride) {
        clearFlightName();
      }
      return;
    }

    // Any other key cancels wipe confirmation
    if (wipeConfirm) {
      setWipeConfirm(false);
    }
  });

  const usedMB = total - free;
  const usedPct = total > 0 ? Math.round((usedMB / total) * 100) : 0;
  const barLen = 40;
  const filledLen = total > 0 ? Math.round((usedMB / total) * barLen) : 0;
  const capacityBar = '\u2588'.repeat(filledLen) + '\u2591'.repeat(barLen - filledLen);

  // Separate flight folders from legacy .bin files
  const legacyBins = rootFiles.filter((f) => f.name.endsWith('.bin'));
  const systemFiles = rootFiles.filter((f) => !f.name.endsWith('.bin'));

  return (
    <Box flexDirection="column" width={DASH_WIDTH + 2}>
      <Header title="SD CARD MANAGER" width={DASH_WIDTH} />

      <Box flexDirection="row">
        {/* Left: Flight listing */}
        <Box flexDirection="column" width={LEFT_W}>
          <Panel
            title={`FLIGHTS  ${flights.length} folder(s)${legacyBins.length > 0 ? ` + ${legacyBins.length} legacy` : ''}`}
            width={LEFT_W}
            borderColor={loading ? 'yellow' : 'blue'}
          >
            {loading && (
              <Text color="yellow">
                {' '}<Spinner type="dots" /> Reading SD card...
              </Text>
            )}

            {error && <Text color="red"> {error}</Text>}

            {!loading && flights.length === 0 && legacyBins.length === 0 && !error && (
              <Text dimColor> No flights found.</Text>
            )}

            {!loading && flights.length > 0 && (
              <>
                <Text dimColor>
                  {' '}{'Folder'.padEnd(24)}{'Size'.padStart(10)}{'  '}{'Contents'}
                </Text>
                {flights.map((f) => (
                  <React.Fragment key={f.name}>
                    <Text>
                      {' '}<Text bold color="cyan">{f.name.padEnd(24)}</Text>
                      <Text>{formatSize(f.totalSize).padStart(10)}</Text>
                      {'  '}
                      {f.hasBin && <Text color="green">BIN</Text>}
                      {f.hasBin && f.hasPreflight && <Text dimColor> + </Text>}
                      {f.hasPreflight && <Text color="yellow">PRE</Text>}
                      {f.files.length > 2 && <Text dimColor> +{f.files.length - 2} more</Text>}
                    </Text>
                  </React.Fragment>
                ))}
              </>
            )}

            {!loading && legacyBins.length > 0 && (
              <>
                <Text>{' '}</Text>
                <Text dimColor> Legacy files (pre-folder):</Text>
                {legacyBins.map((f) => (
                  <Text key={f.name} dimColor>
                    {' '}{f.name.padEnd(24)}{formatSize(f.size).padStart(10)}
                  </Text>
                ))}
              </>
            )}

            {!loading && systemFiles.length > 0 && (
              <>
                <Text>{' '}</Text>
                <Text dimColor> System: {systemFiles.map((f) => f.name).join(', ')}</Text>
              </>
            )}
          </Panel>
        </Box>

        <Box width={2}><Text>{'  '}</Text></Box>

        {/* Right: Capacity + Next flight */}
        <Box flexDirection="column" width={RIGHT_W}>
          <Panel title="CAPACITY" width={RIGHT_W} borderColor={free < 50 ? 'red' : 'green'}>
            <Text> <Text color={free < 50 ? 'red' : 'cyan'}>{capacityBar}</Text></Text>
            <Text>
              {' '}<Text bold>{usedMB} MB</Text> used of <Text bold>{total} MB</Text>
              {' '}({usedPct}%)
            </Text>
            <Text> <Text bold color="green">{free} MB</Text> free</Text>
          </Panel>

          <Text>{' '}</Text>

          <Panel title="NEXT FLIGHT" width={RIGHT_W} borderColor={nameOverride ? 'yellow' : 'blue'}>
            <Text>
              {' '}Path  <Text bold color="cyan">{nextFile || '--'}</Text>
            </Text>
            {nameOverride ? (
              <>
                <Text>{' '}</Text>
                <Text color="yellow"> Override: <Text bold>{nameOverride}</Text></Text>
                <Text dimColor> Press [C] to clear override</Text>
              </>
            ) : (
              <>
                <Text>{' '}</Text>
                <Text dimColor> Default auto-increment naming</Text>
                <Text dimColor> Press [N] to set custom name</Text>
              </>
            )}
          </Panel>
        </Box>
      </Box>

      <Text>{' '}</Text>

      {/* Status messages */}
      {wipeConfirm && (
        <Text backgroundColor="red" color="black" bold>
          {'  WIPE FLIGHTS — Press [W] again to delete all flight folders. Any other key to cancel.'.padEnd(DASH_WIDTH)}
        </Text>
      )}

      {wipeStatus === 'wiping' && (
        <Text color="yellow">{'  '}<Spinner type="dots" /> {wipeMessage}</Text>
      )}

      {wipeStatus === 'done' && (
        <Text color="green" bold>{'  '}{wipeMessage}</Text>
      )}

      {wipeStatus === 'error' && (
        <Text color="red" bold>{'  '}{wipeMessage}</Text>
      )}

      {naming && (
        <Box>
          <Text backgroundColor="blue" color="black" bold>
            {'  Flight name: '.padEnd(20)}
          </Text>
          <Text backgroundColor="blueBright" color="black" bold>
            {` ${nameInput}_ `.padEnd(DASH_WIDTH - 20)}
          </Text>
        </Box>
      )}

      {naming && (
        <Text dimColor>{'  '}Type a name (a-z, 0-9, _ , -) then Enter. Esc to cancel.</Text>
      )}

      <KeyBar
        keys={
          naming
            ? [['Enter', 'Confirm'], ['Esc', 'Cancel']]
            : [
                ['S', 'Back'],
                ['R', 'Refresh'],
                ['N', nameOverride ? 'Change Name' : 'Set Name'],
                ...(nameOverride ? [['C', 'Clear Name'] as [string, string]] : []),
                ['W', 'Wipe Flights'],
                ['Q', 'Quit'],
              ]
        }
        width={DASH_WIDTH}
      />
    </Box>
  );
}
