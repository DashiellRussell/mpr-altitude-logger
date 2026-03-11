import React from 'react';
import { Text } from 'ink';
import Spinner from 'ink-spinner';

interface CheckItemProps {
  name: string;
  status: 'pending' | 'running' | 'pass' | 'fail' | 'skip';
  detail: string;
  /** Extra descriptive line shown below the check (e.g. expected values) */
  hint?: string;
  /** Max character width for truncation */
  maxWidth?: number;
}

/**
 * Hardware check row with status badge + optional detail line.
 * Returns a Fragment so each line becomes a separate Panel child
 * and gets its own │ border │ wrapper.
 */
export function CheckItem({ name, status, detail, hint, maxWidth }: CheckItemProps) {
  const detailMax = maxWidth ? maxWidth - 10 : 44; // 10 = indent

  let badge: React.ReactNode;
  switch (status) {
    case 'pass':
      badge = <Text color="green" bold>{' PASS '}</Text>;
      break;
    case 'fail':
      badge = <Text color="red" bold>{' FAIL '}</Text>;
      break;
    case 'skip':
      badge = <Text color="yellow">{' SKIP '}</Text>;
      break;
    case 'running':
      badge = <Text color="yellow"><Spinner type="dots" />{' ... '}</Text>;
      break;
    case 'pending':
    default:
      badge = <Text dimColor>{' ---- '}</Text>;
      break;
  }

  const bracketColor = status === 'pass' ? 'green' : status === 'fail' ? 'red' : status === 'running' ? 'yellow' : undefined;
  const bracketBold = status === 'pass' || status === 'fail';

  return (
    <>
      <Text>
        {' '}
        <Text color={bracketColor} bold={bracketBold}>{'['}</Text>
        {badge}
        <Text color={bracketColor} bold={bracketBold}>{']'}</Text>
        {'  '}
        <Text bold>{name}</Text>
      </Text>
      {detail ? (
        <Text dimColor>{'          '}{detail.length > detailMax ? detail.slice(0, detailMax - 1) + '\u2026' : detail}</Text>
      ) : null}
      {hint && status === 'pending' ? (
        <Text dimColor>{'          '}{hint.length > detailMax ? hint.slice(0, detailMax - 1) + '\u2026' : hint}</Text>
      ) : null}
    </>
  );
}
