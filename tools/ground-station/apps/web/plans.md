# SpaceX-Style Flight Review Dashboard — Implementation Plan

## Layout

```
+--------------------------------------------------+
|  MPR ALTITUDE LOGGER — FLIGHT REVIEW              |
|  [file] [Export] [+ Sim] [Launch View|Analysis]    |
+----------------------+---------------------------+
|                      |  TELEMETRY PANEL           |
|   3D ROCKET SCENE    |  ALT  1,247 m             |
|   (React Three Fiber)|  VEL  186.3 m/s           |
|                      |  STATE: COAST              |
|   Rocket animates    +---------------------------+
|   through flight     |  CHART TABS                |
|   states with        |  [Overview|Alt|Vel|Prs|Pwr]|
|   exhaust + chutes   |  (active chart + cursor)   |
+----------------------+---------------------------+
|  [|<] [>] [1x] ====[======]=============== T+12.3 |
+--------------------------------------------------+
```

## New Dependencies

- `three` — 3D engine
- `@react-three/fiber` — React wrapper for Three.js
- `@react-three/drei` — R3F helpers (OrbitControls, Trail, Environment, Text, Sparkles)
- `@types/three` — dev dep

## New Files (10)

| File | Purpose |
|------|---------|
| `hooks/usePlayback.ts` | RAF-based playback engine (play/pause, 0.25x–4x speed, seek, keyboard) |
| `components/TelemetryReadout.tsx` | Single SpaceX-style big number + label |
| `components/TelemetryPanel.tsx` | Grid of readouts driven by current frame |
| `components/FlightStateBadge.tsx` | Colored state indicator with glow on transition |
| `components/PlaybackBar.tsx` | Full-width bottom bar: timeline scrubber with state color bands, controls |
| `components/ChartTabs.tsx` | Tab container for all chart views |
| `components/rocket/RocketScene.tsx` | R3F Canvas + camera auto-tracking |
| `components/rocket/RocketModel.tsx` | Procedural rocket (cylinder body, cone nose, fins, engine bell) |
| `components/rocket/ExhaustFlame.tsx` | Instanced particle flame during BOOST |
| `components/rocket/Parachute.tsx` | Chute geometry for DROGUE/MAIN |

## Modified Files

| File | Change |
|------|--------|
| `package.json` | Add three/R3F deps |
| `App.tsx` | Two-column layout, Launch/Analysis view toggle |
| `styles/globals.css` | SpaceX dark aesthetic overhaul |
| `AltitudeChart.tsx` | Add `cursorTime` prop for playback sync |
| `VelocityChart.tsx` | Add `cursorTime` prop |
| `PressureChart.tsx` | Add `cursorTime` prop |
| `PowerChart.tsx` | Add `cursorTime` prop |
| `FlightOverview.tsx` | Add `cursorTime` prop |

## Rocket Animation by State

- **PAD**: Static on ground, upright, no effects
- **BOOST**: Rises (y = altitude/maxAlt * sceneHeight), vibration jitter, exhaust flame particles, camera pulls back logarithmically
- **COAST**: Continues upward, flame off, begins tilting (5–10deg pitch based on velocity decay)
- **APOGEE**: Peak altitude, ~45deg tilt, momentary pause
- **DROGUE**: Small orange chute appears (half-sphere + lines), descending, rocket hangs below
- **MAIN**: Larger white chute replaces drogue, slower descent
- **LANDED**: Settles at y=0, upright or slightly tilted, all effects off

## Playback System (`usePlayback` hook)

```typescript
interface PlaybackState {
  isPlaying: boolean;
  speed: number;           // 0.25 | 0.5 | 1 | 2 | 4
  currentIndex: number;    // index into frames[]
  currentFrame: FlightFrame | null;
  currentTime: number;     // seconds since T0
  progress: number;        // 0..1
}

interface PlaybackControls {
  play: () => void;
  pause: () => void;
  toggle: () => void;
  setSpeed: (s: number) => void;
  seekToIndex: (i: number) => void;
  seekToTime: (t: number) => void;
  seekToProgress: (p: number) => void;
  reset: () => void;
}
```

- Driven by `requestAnimationFrame`, advances index based on elapsed real time × speed
- Auto-pauses at end of flight
- Binary search for time-based seeking
- Only updates `currentFrame` state when index actually changes (~25Hz, not 60fps)

## Rocket Model (Procedural Geometry)

Built from Three.js primitives — no external GLTF assets:
- **Body tube**: CylinderGeometry, white/grey material
- **Nosecone**: ConeGeometry, same material
- **Fins**: 3–4 ExtrudeGeometry from trapezoidal Shape, positioned radially
- **Engine bell**: Small ConeGeometry at base, darker material

Rotation uses `useFrame` with lerp toward target rotation each render frame.

## Exhaust Flame (BOOST only)

InstancedMesh particles (~20–50 spheres):
- Spawn at engine bell, move downward, scale up, fade out
- Orange-to-transparent color ramp
- Intensity scales with velocity
- Fallback: emissive cone mesh + `<Sparkles>` from drei

## Parachute (DROGUE/MAIN)

- Half-sphere or cone connected to rocket body by `<Line>` segments
- DROGUE: small, orange
- MAIN: larger, white
- Scale-up animation on state transition

## Chart Cursor Sync

All chart components gain optional `cursorTime?: number` prop:
- Renders a bright white vertical `ReferenceLine` at that time
- Small label showing the value at that point
- Lightweight — only the ReferenceLine re-renders, not the full chart data

## CSS Changes

- Background: `#000000` with subtle radial gradient noise
- Cards: sharper edges (minimal border-radius), near-invisible borders
- Telemetry numbers: 40–60px, thin weight, wide letter-spacing, monospace
- Small caps labels above numbers
- State badge: colored with glow effect
- Playback bar: glass-morphism (backdrop-filter blur), semi-transparent
- SpaceX blue accent: `#005288` for primary UI elements
- Existing state/accent colors kept as-is

## Dual Views

- **Launch View**: SpaceX two-column layout (3D scene + telemetry + tabbed charts + playback bar)
- **Analysis View**: Current vertical card layout (FlightSummary, all charts, SimComparison, FlightInsights)
- Toggle in header

## Performance

- `React.lazy` for R3F scene — file upload screen stays fast (~150KB gzipped saved on initial load)
- `frameloop="demand"` on Canvas when paused, `"always"` during playback
- InstancedMesh for particles (max 50), not individual meshes
- Charts: `React.memo` with comparator that skips `cursorTime` for heavy recalculations
- Playback hook only fires state update when frame index changes (~25Hz), not every RAF tick (60Hz)
- WebGL fallback: if context fails, hide 3D panel, show static altitude indicator

## Implementation Order

1. [x] Add R3F deps to package.json, verify build
2. [x] Build `usePlayback` hook (testable, no rendering)
3. [x] Build `PlaybackBar` with scrubber UX
4. [x] Build `TelemetryReadout` + `TelemetryPanel`
5. [x] Build static `RocketModel` (all state poses, no animation)
6. [x] Build `RocketScene` with model + camera
7. [x] Wire playback → scene + telemetry (first animated playback)
8. [x] Add `ExhaustFlame` + `Parachute` effects
9. [x] Add `cursorTime` to all chart components
10. [x] Build `ChartTabs` container
11. [x] Restructure `App.tsx` into two-column layout
12. [x] CSS overhaul for SpaceX aesthetic
13. [x] Add Launch View / Analysis View toggle
14. [x] Performance tuning (lazy load, frameloop, memo, RAF throttle)
15. [x] Keyboard shortcuts (Space = play/pause, ←/→ = step frame, Shift+arrows = jump 1s)

## Risks

| Risk | Mitigation |
|------|------------|
| Three.js bundle size (~150KB gz) | Dynamic import with React.lazy |
| Mobile/low-end GPU | No shadows, no post-processing, max 50 particles. WebGL fallback |
| Recharts + R3F CPU contention | Only update chart cursor, not full redraw. Pause R3F when idle |
