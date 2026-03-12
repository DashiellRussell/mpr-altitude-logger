import React, { Suspense, useRef } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls, Stars, Grid } from '@react-three/drei';
import * as THREE from 'three';
import { RocketModel } from './RocketModel';
import { ExhaustFlame } from './ExhaustFlame';
import { Parachute } from './Parachute';

interface RocketSceneProps {
  state: string;
  altitude: number;
  velocity: number;
  maxAlt: number;
  isPlaying: boolean;
}

function RocketAssembly({ state, altitude, velocity, maxAlt }: Omit<RocketSceneProps, 'isPlaying'>) {
  const groupRef = useRef<THREE.Group>(null!);

  const sceneHeight = 8;
  const targetY = maxAlt > 0 ? (altitude / maxAlt) * sceneHeight : 0;

  useFrame((_, delta) => {
    if (!groupRef.current) return;
    groupRef.current.position.y = THREE.MathUtils.lerp(
      groupRef.current.position.y,
      targetY,
      delta * 3
    );
  });

  const chuteType = state === 'DROGUE' ? 'drogue' as const
    : state === 'MAIN' ? 'main' as const
    : null;

  const exhaustIntensity = state === 'BOOST' ? Math.min(1, Math.abs(velocity) / 150) : 0;

  return (
    <group ref={groupRef}>
      <RocketModel state={state} velocity={velocity} altitude={altitude} maxAlt={maxAlt} />
      <ExhaustFlame active={state === 'BOOST'} intensity={exhaustIntensity} />
      <Parachute type={chuteType} />
    </group>
  );
}

function CameraTracker({ altitude, maxAlt, state }: { altitude: number; maxAlt: number; state: string }) {
  const smoothPos = useRef(new THREE.Vector3(5, 2, 5));
  const smoothTarget = useRef(new THREE.Vector3(0, 0, 0));

  useFrame(({ camera }) => {
    const sceneHeight = 8;
    const rocketY = maxAlt > 0 ? (altitude / maxAlt) * sceneHeight : 0;

    const hasChute = state === 'DROGUE' || state === 'MAIN';
    const isCoast = state === 'COAST' || state === 'APOGEE';
    // Pulled back further overall (+2 base)
    const baseDist = hasChute ? 8 : isCoast ? 7.5 : 6;
    const heightFactor = Math.log1p(rocketY) * 0.8;
    const dist = baseDist + heightFactor;

    // Camera slightly below rocket level but always above ground
    const camY = Math.max(0.5, rocketY * 0.7) + (hasChute ? 0.5 : 0);

    const goalPos = new THREE.Vector3(dist * 0.65, camY, dist * 0.65);

    // Look target slightly above the rocket so the view angles upward
    const lookY = rocketY + 0.5 + (hasChute ? 1.0 : 0);
    const goalTarget = new THREE.Vector3(0, lookY, 0);

    smoothPos.current.lerp(goalPos, 0.02);
    smoothTarget.current.lerp(goalTarget, 0.025);

    camera.position.copy(smoothPos.current);
    camera.lookAt(smoothTarget.current);
  });

  return null;
}

export function RocketScene({ state, altitude, velocity, maxAlt, isPlaying }: RocketSceneProps) {
  return (
    <div className="rocket-scene-container">
      <Canvas
        camera={{ position: [5, 2, 5], fov: 50 }}
        frameloop={isPlaying ? 'always' : 'demand'}
        gl={{ antialias: true, alpha: true }}
        style={{ background: 'transparent' }}
      >
        <Suspense fallback={null}>
          <ambientLight intensity={0.4} />
          <directionalLight position={[5, 10, 5]} intensity={0.8} />
          <pointLight position={[0, -2, 0]} intensity={state === 'BOOST' ? 3 : 0} color="#ff6600" distance={8} />

          <Stars radius={50} depth={30} count={800} factor={3} fade speed={0.5} />

          {/* Ground plane — visible dark surface */}
          <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.02, 0]}>
            <planeGeometry args={[60, 60]} />
            <meshStandardMaterial color="#0c0c0c" metalness={0.05} roughness={0.9} />
          </mesh>
          <Grid
            position={[0, -0.01, 0]}
            args={[60, 60]}
            cellSize={1}
            cellThickness={0.8}
            cellColor="#1a1a1a"
            sectionSize={5}
            sectionThickness={1.5}
            sectionColor="#333333"
            fadeDistance={30}
            fadeStrength={1.5}
            infiniteGrid
          />

          {/* Launch rail */}
          <mesh position={[0.2, 1, 0]}>
            <cylinderGeometry args={[0.005, 0.005, 2, 4]} />
            <meshStandardMaterial color="#333" />
          </mesh>

          <RocketAssembly state={state} altitude={altitude} velocity={velocity} maxAlt={maxAlt} />
          <CameraTracker altitude={altitude} maxAlt={maxAlt} state={state} />
          <OrbitControls enablePan={false} enableZoom={true} maxDistance={20} minDistance={2} />
        </Suspense>
      </Canvas>
    </div>
  );
}
