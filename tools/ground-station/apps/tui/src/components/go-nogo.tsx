import React from 'react';
import { Box, Text } from 'ink';

interface CheckInfo {
  name: string;
  status: 'pending' | 'running' | 'pass' | 'fail' | 'skip';
  detail: string;
}

interface GoNogoProps {
  checks: CheckInfo[];
  voltagesOk: boolean;
  baroSane: boolean;
  sdFree?: number;
  issues?: string[];
  width?: number;
  manualOverride?: boolean;
}

/** Full-width GO FOR LAUNCH or NO-GO banner */
export function GoNogo({ checks, voltagesOk, baroSane, sdFree = 100, issues = [], width = 120, manualOverride = false }: GoNogoProps) {
  const allPassed = checks.every((c) => c.status === 'pass' || c.status === 'skip');
  const nPass = checks.filter((c) => c.status === 'pass' || c.status === 'skip').length;

  const naturalGo = allPassed && voltagesOk && baroSane && sdFree > 10 && issues.length === 0;
  const go = naturalGo || manualOverride;

  const innerW = width - 2;

  if (naturalGo) {
    const line1 = `  \u2605  GO FOR LAUNCH  \u2605`;
    const line2 = `  All ${nPass} checks passed  \u2022  Systems nominal`;
    return (
      <Box flexDirection="column">
        <Text backgroundColor="green" color="white" bold>
          {line1.padEnd(innerW)}
        </Text>
        <Text backgroundColor="green" color="white" bold>
          {line2.padEnd(innerW)}
        </Text>
      </Box>
    );
  }

  if (manualOverride) {
    // Build override warnings
    const warnings: string[] = [];
    if (!allPassed) {
      const failed = checks.filter((c) => c.status === 'fail').map((c) => c.name);
      if (failed.length) warnings.push(`Failed: ${failed.join(', ')}`);
    }
    if (!voltagesOk) warnings.push('Voltage override');
    if (!baroSane) warnings.push('Baro override');
    if (sdFree <= 10) warnings.push('SD low');
    warnings.push(...issues);

    const warnStr = warnings.length ? warnings.slice(0, 3).join('  \u2022  ') : 'Manual override active';

    return (
      <Box flexDirection="column">
        <Text backgroundColor="yellow" color="black" bold>
          {`  \u26A0  GO FOR LAUNCH (MANUAL OVERRIDE)  \u26A0`.padEnd(innerW)}
        </Text>
        <Text backgroundColor="yellow" color="black" bold>
          {`  ${warnStr}`.padEnd(innerW)}
        </Text>
      </Box>
    );
  }

  // Build reasons
  const reasons: string[] = [...issues];
  if (!allPassed) {
    const failed = checks.filter((c) => c.status === 'fail').map((c) => c.name);
    if (failed.length) reasons.push(`Failed: ${failed.join(', ')}`);
  }
  if (!voltagesOk) reasons.push('Voltage rail out of spec');
  if (!baroSane) reasons.push('Barometer reading out of range');
  if (sdFree <= 10 && sdFree > 0) reasons.push('SD card low space');

  const unique = [...new Set(reasons)];
  const reasonStr = unique.slice(0, 3).join('  \u2022  ') || 'Check failures';

  return (
    <Box flexDirection="column">
      <Text backgroundColor="red" color="white" bold>
        {`  \u2717  NO-GO  \u2717`.padEnd(innerW)}
      </Text>
      <Text backgroundColor="red" color="white" bold>
        {`  ${reasonStr}`.padEnd(innerW)}
      </Text>
    </Box>
  );
}
