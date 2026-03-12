import React, { useCallback, useRef } from 'react';
import type { FlightStats } from '@mpr/shared';
import type { PlaybackState, PlaybackControls } from '../hooks/usePlayback';
import { SPEED_OPTIONS } from '../hooks/usePlayback';

const STATE_BAR_COLORS: Record<string, string> = {
  PAD: '#888888',
  BOOST: '#ff4444',
  COAST: '#ffaa00',
  APOGEE: '#44ff44',
  DROGUE: '#44ffff',
  MAIN: '#4488ff',
  LANDED: '#ff44ff',
};

interface PlaybackBarProps {
  playback: PlaybackState;
  controls: PlaybackControls;
  stats: FlightStats;
  totalDuration: number;
}

export function PlaybackBar({ playback, controls, stats, totalDuration }: PlaybackBarProps) {
  const trackRef = useRef<HTMLDivElement>(null);

  // Compute state color bands for the track
  const stateBands = React.useMemo(() => {
    if (!stats.transitions.length || totalDuration <= 0) return [];
    const bands: { state: string; start: number; end: number }[] = [];
    // First band: PAD to first transition
    const allTimes = [0, ...stats.transitions.map(t => t.time), totalDuration];
    const allStates = ['PAD', ...stats.transitions.map(t => t.to_state)];

    for (let i = 0; i < allStates.length; i++) {
      bands.push({
        state: allStates[i],
        start: allTimes[i] / totalDuration,
        end: allTimes[i + 1] / totalDuration,
      });
    }
    return bands;
  }, [stats.transitions, totalDuration]);

  const handleTrackClick = useCallback((e: React.MouseEvent) => {
    if (!trackRef.current) return;
    const rect = trackRef.current.getBoundingClientRect();
    const p = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    controls.seekToProgress(p);
  }, [controls]);

  const handleTrackDrag = useCallback((e: React.MouseEvent) => {
    if (e.buttons !== 1) return;
    handleTrackClick(e);
  }, [handleTrackClick]);

  const cycleSpeed = useCallback(() => {
    const idx = SPEED_OPTIONS.indexOf(playback.speed as typeof SPEED_OPTIONS[number]);
    const next = SPEED_OPTIONS[(idx + 1) % SPEED_OPTIONS.length];
    controls.setSpeed(next);
  }, [playback.speed, controls]);

  return (
    <div className="playback-bar">
      <button className="playback-btn" onClick={() => controls.seekToIndex(0)} title="Reset">
        {'|<'}
      </button>
      <button className="playback-btn playback-btn-play" onClick={controls.toggle} title={playback.isPlaying ? 'Pause' : 'Play'}>
        {playback.isPlaying ? '||' : '>'}
      </button>
      <button className="playback-btn playback-speed" onClick={cycleSpeed} title="Playback speed">
        {playback.speed}x
      </button>

      <div
        className="playback-track"
        ref={trackRef}
        onClick={handleTrackClick}
        onMouseMove={handleTrackDrag}
      >
        {/* State color bands */}
        <div className="playback-bands">
          {stateBands.map((band, i) => (
            <div
              key={i}
              className="playback-band"
              style={{
                left: `${band.start * 100}%`,
                width: `${(band.end - band.start) * 100}%`,
                background: STATE_BAR_COLORS[band.state] ?? '#333',
              }}
            />
          ))}
        </div>
        {/* Cursor */}
        <div
          className="playback-cursor"
          style={{ left: `${playback.progress * 100}%` }}
        />
      </div>

      <span className="playback-time">
        T+{playback.currentTime.toFixed(1)}s
      </span>
    </div>
  );
}
