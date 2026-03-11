import React from 'react';
import { Box, Text } from 'ink';

interface PanelProps {
  title: string;
  width?: number;
  borderColor?: string;
  children: React.ReactNode;
}

/** Recursively flatten React Fragments so each leaf gets its own border row */
function flattenChildren(children: React.ReactNode): React.ReactNode[] {
  const flat: React.ReactNode[] = [];
  React.Children.forEach(children, (child) => {
    if (!React.isValidElement(child)) {
      if (child != null && child !== false) flat.push(child);
      return;
    }
    if (child.type === React.Fragment) {
      flat.push(...flattenChildren(child.props.children));
    } else {
      flat.push(child);
    }
  });
  return flat;
}

/**
 * Bordered panel with title — box-drawing characters.
 * Each child gets its own bordered row. Uses fixed width Box
 * with flexShrink=0 to prevent content from breaking borders.
 */
export function Panel({ title, width, borderColor = 'blue', children }: PanelProps) {
  const w = width ?? 56;
  const innerWidth = w - 2; // minus left/right border chars

  // Title bar: ┌─ TITLE ─────────┐
  const titleStr = ` ${title} `;
  const remainingDashes = Math.max(0, innerWidth - titleStr.length - 1);
  const topBar = `\u250c\u2500${titleStr}${'\u2500'.repeat(remainingDashes)}\u2510`;

  // Bottom bar: └─────────────────┘
  const bottomBar = `\u2514${'\u2500'.repeat(innerWidth)}\u2518`;

  const rows = flattenChildren(children);

  return (
    <Box flexDirection="column" width={w}>
      <Text color={borderColor}>{topBar}</Text>
      {rows.map((child, i) => (
        <Box key={i} height={1} width={w} flexShrink={0}>
          <Text color={borderColor}>{'\u2502'}</Text>
          <Box width={innerWidth} height={1} overflowX="hidden" flexShrink={0}>
            {child}
          </Box>
          <Text color={borderColor}>{'\u2502'}</Text>
        </Box>
      ))}
      <Text color={borderColor}>{bottomBar}</Text>
    </Box>
  );
}
