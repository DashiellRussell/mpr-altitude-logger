import React, { useState } from 'react';
import { Box, Text, useInput, useApp } from 'ink';
import { writeFileSync } from 'fs';
import { framesToCsv } from '@mpr/shared';
import { useFlightData } from '../hooks/use-flight-data.js';
import { Header } from '../components/header.js';
import { FlightSummary } from '../components/flight-summary.js';
import { AltitudeChart } from '../components/altitude-chart.js';
import { StateTimeline } from '../components/state-timeline.js';
import { SimCompare } from '../components/sim-compare.js';
import { KeyBar } from '../components/key-bar.js';
import { Download } from './download.js';
import { sparkline } from '@mpr/shared';

interface PostflightProps {
  binFile?: string;
  simFile?: string;
  port?: string;
}

export function Postflight({ binFile, simFile, port }: PostflightProps) {
  const { exit } = useApp();
  const [downloadedFile, setDownloadedFile] = useState<string | undefined>(binFile);
  const [statusMsg, setStatusMsg] = useState('');

  // If no bin file, show download screen first
  if (!downloadedFile) {
    return (
      <Download
        port={port}
        onComplete={(filePath) => setDownloadedFile(filePath)}
        onCancel={() => exit()}
      />
    );
  }

  return (
    <PostflightDashboard
      binFile={downloadedFile}
      simFile={simFile}
    />
  );
}

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

  useInput((input, _key) => {
    if (input === 'q' || input === 'Q') {
      exit();
    }
    if ((input === 'e' || input === 'E') && stats && frames.length) {
      // Export CSV
      const csvPath = binFile.replace(/\.bin$/i, '.csv') || 'flight_export.csv';
      try {
        const csv = framesToCsv(frames, version);
        writeFileSync(csvPath, csv);
        setStatusMsg(`Exported ${frames.length} frames to ${csvPath}`);
      } catch (e) {
        setStatusMsg(`Export error: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
    if ((input === 's' || input === 'S') && stats) {
      // Save summary
      const txtPath = binFile.replace(/\.bin$/i, '.txt') || 'flight_summary.txt';
      try {
        const lines: string[] = [];
        lines.push('MPR ALTITUDE LOGGER -- FLIGHT SUMMARY');
        lines.push('='.repeat(50));
        lines.push(`Apogee:        ${stats.maxAlt.toFixed(1)} m AGL @ T+${stats.maxAltTime.toFixed(2)}s`);
        lines.push(`Max Velocity:  ${stats.maxVel.toFixed(1)} m/s @ T+${stats.maxVelTime.toFixed(2)}s`);
        lines.push(`Max Accel:     ~${stats.maxAccel.toFixed(1)} m/s^2 (est.)`);
        lines.push(`Flight Time:   ${stats.duration.toFixed(1)} s`);
        lines.push(`Sample Rate:   ${stats.sampleRate.toFixed(1)} Hz (${stats.nFrames} frames)`);
        lines.push(`Landing Vel:   ${stats.landingVel.toFixed(1)} m/s`);
        lines.push('');
        lines.push(`Drogue:  ${stats.drogueFired ? `FIRED @ T+${stats.drogueTime?.toFixed(2)}s` : 'NOT FIRED'}`);
        lines.push(`Main:    ${stats.mainFired ? `FIRED @ T+${stats.mainTime?.toFixed(2)}s` : 'NOT FIRED'}`);
        lines.push(`Armed:   ${stats.wasArmed ? 'YES' : 'NO'}`);
        lines.push(`Errors:  ${stats.hadError ? 'YES' : 'NONE'}`);
        lines.push('');
        lines.push('State Transitions:');
        for (const tr of stats.transitions) {
          lines.push(`  T+${tr.time.toFixed(2).padStart(7)}s  ${tr.from_state} -> ${tr.to_state}`);
        }
        writeFileSync(txtPath, lines.join('\n') + '\n');
        setStatusMsg(`Saved summary to ${txtPath}`);
      } catch (e) {
        setStatusMsg(`Save error: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
  });

  if (loading) {
    return (
      <Box flexDirection="column">
        <Header title="POST-FLIGHT ANALYSIS" />
        <Text color="yellow">  Loading {binFile}...</Text>
      </Box>
    );
  }

  if (error && !stats) {
    return (
      <Box flexDirection="column">
        <Header title="POST-FLIGHT ANALYSIS" />
        <Text color="red">  Error: {error}</Text>
        <KeyBar keys={[['Q', 'Quit']]} />
      </Box>
    );
  }

  if (!stats || !frames.length) {
    return (
      <Box flexDirection="column">
        <Header title="POST-FLIGHT ANALYSIS" />
        <Text color="red">  No valid frames found in {binFile}</Text>
        <KeyBar keys={[['Q', 'Quit']]} />
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

  return (
    <Box flexDirection="column">
      <Header title="POST-FLIGHT ANALYSIS" />
      <Text dimColor>  {binFile}  |  {frames.length.toLocaleString()} frames  |  log v{version}
        {skippedBytes > 0 ? `  |  ${skippedBytes} bytes skipped` : ''}
      </Text>
      <Text> </Text>

      {/* Flight summary */}
      <FlightSummary stats={stats} />

      {/* Altitude chart */}
      <AltitudeChart
        frames={frames}
        sim={sim}
        transitions={stats.transitions}
      />

      {/* State timeline */}
      <StateTimeline stats={stats} frames={frames} />

      {/* Velocity sparkline */}
      <Box flexDirection="column" marginBottom={1}>
        <Text bold>VELOCITY PROFILE</Text>
        <Text>
          {'  Vel  '}
          <Text color="cyan">{velSpark}</Text>
          {'  '}
          <Text color="cyan">{stats.maxVel > 0 ? '+' : ''}{stats.maxVel.toFixed(1)} m/s peak</Text>
        </Text>
      </Box>

      {/* Power rails */}
      {powerContent}

      {/* Sim comparison */}
      {sim && <SimCompare stats={stats} sim={sim} />}

      {/* Warning for error */}
      {error && <Text color="yellow">  {error}</Text>}

      {/* Status message */}
      {statusMsg && <Text color="green">  {statusMsg}</Text>}

      <KeyBar
        keys={[
          ['E', 'Export CSV'],
          ['S', 'Save Summary'],
          ['Q', 'Quit'],
        ]}
      />
    </Box>
  );
}
