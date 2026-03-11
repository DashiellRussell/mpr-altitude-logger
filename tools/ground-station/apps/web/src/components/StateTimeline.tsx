import React, { useMemo } from 'react';
import type { FlightStats } from '@mpr/shared';

const STATE_COLORS_HEX: Record<string, string> = {
  PAD: '#888888',
  BOOST: '#ff4444',
  COAST: '#ffaa00',
  APOGEE: '#44ff44',
  DROGUE: '#44ffff',
  MAIN: '#4488ff',
  LANDED: '#ff44ff',
};

interface StateTimelineProps {
  stats: FlightStats;
}

interface Segment {
  state: string;
  start: number;
  duration: number;
  fraction: number;
}

export function StateTimeline({ stats }: StateTimelineProps) {
  const segments = useMemo(() => {
    const transitions = stats.transitions;
    const total = stats.duration;
    if (total <= 0) return [];

    const segs: Segment[] = [];

    // First segment: from 0 to first transition
    const firstState = transitions.length > 0 ? transitions[0].from_state : 'PAD';
    const firstEnd = transitions.length > 0 ? transitions[0].time : total;
    segs.push({
      state: firstState,
      start: 0,
      duration: firstEnd,
      fraction: firstEnd / total,
    });

    // Middle segments
    for (let i = 0; i < transitions.length; i++) {
      const t = transitions[i];
      const nextTime = i + 1 < transitions.length ? transitions[i + 1].time : total;
      const dur = nextTime - t.time;
      segs.push({
        state: t.to_state,
        start: t.time,
        duration: dur,
        fraction: dur / total,
      });
    }

    return segs;
  }, [stats]);

  if (segments.length === 0) {
    return <div className="state-timeline" style={{ background: '#2a2a3e', borderRadius: 6, height: 36 }} />;
  }

  return (
    <div className="state-timeline">
      {segments.map((seg, i) => (
        <div
          key={i}
          className="state-segment"
          style={{
            width: `${Math.max(seg.fraction * 100, 0.5)}%`,
            backgroundColor: STATE_COLORS_HEX[seg.state] ?? '#888',
          }}
          title={`${seg.state}: ${seg.duration.toFixed(1)}s (${(seg.fraction * 100).toFixed(1)}%)`}
        >
          {seg.fraction > 0.06 ? seg.state : ''}
        </div>
      ))}
    </div>
  );
}
