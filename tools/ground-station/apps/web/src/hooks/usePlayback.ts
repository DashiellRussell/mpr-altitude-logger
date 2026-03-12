import { useState, useCallback, useRef, useEffect } from 'react';
import type { FlightFrame } from '@mpr/shared';

const SPEED_OPTIONS = [0.25, 0.5, 1, 2, 4] as const;

export interface PlaybackState {
  isPlaying: boolean;
  speed: number;
  currentIndex: number;
  currentFrame: FlightFrame | null;
  currentTime: number;
  progress: number;
}

export interface PlaybackControls {
  play: () => void;
  pause: () => void;
  toggle: () => void;
  setSpeed: (s: number) => void;
  seekToIndex: (i: number) => void;
  seekToTime: (t: number) => void;
  seekToProgress: (p: number) => void;
  reset: () => void;
}

export function usePlayback(frames: FlightFrame[]): [PlaybackState, PlaybackControls] {
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeedState] = useState(1);
  const [currentIndex, setCurrentIndex] = useState(0);

  const rafRef = useRef<number>(0);
  const lastTickRef = useRef<number>(0);
  const framesRef = useRef(frames);
  const speedRef = useRef(speed);
  const indexRef = useRef(currentIndex);

  framesRef.current = frames;
  speedRef.current = speed;
  indexRef.current = currentIndex;

  const t0 = frames.length > 0 ? frames[0].timestamp_ms : 0;
  const tEnd = frames.length > 0 ? frames[frames.length - 1].timestamp_ms : 0;
  const totalDuration = (tEnd - t0) / 1000;

  const currentFrame = frames.length > 0 ? frames[currentIndex] ?? null : null;
  const currentTime = currentFrame ? (currentFrame.timestamp_ms - t0) / 1000 : 0;
  const progress = totalDuration > 0 ? currentTime / totalDuration : 0;

  // Binary search: find frame index closest to a given flight time (seconds since t0)
  const findIndexForTime = useCallback((targetTime: number): number => {
    const ff = framesRef.current;
    if (ff.length === 0) return 0;
    const targetMs = t0 + targetTime * 1000;
    let lo = 0;
    let hi = ff.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (ff[mid].timestamp_ms < targetMs) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }, [t0]);

  // RAF loop
  const tick = useCallback((now: number) => {
    const dt = (now - lastTickRef.current) / 1000; // real seconds elapsed
    lastTickRef.current = now;

    const ff = framesRef.current;
    if (ff.length === 0) return;

    const idx = indexRef.current;
    const frame = ff[idx];
    const flightTimeAdvance = dt * speedRef.current; // flight seconds to advance
    const targetFlightTime = ((frame.timestamp_ms - ff[0].timestamp_ms) / 1000) + flightTimeAdvance;

    const newIdx = findIndexForTime(targetFlightTime);

    if (newIdx >= ff.length - 1) {
      // Reached end
      setCurrentIndex(ff.length - 1);
      indexRef.current = ff.length - 1;
      setIsPlaying(false);
      return;
    }

    if (newIdx !== idx) {
      setCurrentIndex(newIdx);
      indexRef.current = newIdx;
    }

    rafRef.current = requestAnimationFrame(tick);
  }, [findIndexForTime]);

  // Start/stop RAF
  useEffect(() => {
    if (isPlaying) {
      lastTickRef.current = performance.now();
      rafRef.current = requestAnimationFrame(tick);
    } else {
      cancelAnimationFrame(rafRef.current);
    }
    return () => cancelAnimationFrame(rafRef.current);
  }, [isPlaying, tick]);

  // Reset when frames change
  useEffect(() => {
    setCurrentIndex(0);
    indexRef.current = 0;
    setIsPlaying(false);
  }, [frames]);

  const play = useCallback(() => {
    if (framesRef.current.length === 0) return;
    // If at end, restart
    if (indexRef.current >= framesRef.current.length - 1) {
      setCurrentIndex(0);
      indexRef.current = 0;
    }
    setIsPlaying(true);
  }, []);

  const pause = useCallback(() => setIsPlaying(false), []);

  const toggle = useCallback(() => {
    if (isPlaying) pause();
    else play();
  }, [isPlaying, play, pause]);

  const setSpeed = useCallback((s: number) => {
    setSpeedState(s);
    speedRef.current = s;
  }, []);

  const seekToIndex = useCallback((i: number) => {
    const clamped = Math.max(0, Math.min(i, framesRef.current.length - 1));
    setCurrentIndex(clamped);
    indexRef.current = clamped;
  }, []);

  const seekToTime = useCallback((t: number) => {
    const idx = findIndexForTime(t);
    seekToIndex(idx);
  }, [findIndexForTime, seekToIndex]);

  const seekToProgress = useCallback((p: number) => {
    seekToTime(p * totalDuration);
  }, [seekToTime, totalDuration]);

  const reset = useCallback(() => {
    setIsPlaying(false);
    setCurrentIndex(0);
    indexRef.current = 0;
    setSpeedState(1);
    speedRef.current = 1;
  }, []);

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
      switch (e.code) {
        case 'Space':
          e.preventDefault();
          toggle();
          break;
        case 'ArrowRight':
          e.preventDefault();
          if (e.shiftKey) {
            // Jump 1 second forward
            seekToTime(currentTime + 1);
          } else {
            seekToIndex(indexRef.current + 1);
          }
          break;
        case 'ArrowLeft':
          e.preventDefault();
          if (e.shiftKey) {
            seekToTime(Math.max(0, currentTime - 1));
          } else {
            seekToIndex(indexRef.current - 1);
          }
          break;
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [toggle, seekToIndex, seekToTime, currentTime]);

  const state: PlaybackState = { isPlaying, speed, currentIndex, currentFrame, currentTime, progress };
  const controls: PlaybackControls = { play, pause, toggle, setSpeed, seekToIndex, seekToTime, seekToProgress, reset };

  return [state, controls];
}

export { SPEED_OPTIONS };
