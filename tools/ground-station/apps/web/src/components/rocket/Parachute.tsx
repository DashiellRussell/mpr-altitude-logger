import React, { useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { Line } from '@react-three/drei';
import * as THREE from 'three';

interface ParachuteProps {
  type: 'drogue' | 'main' | null;
}

const CHUTE_CONFIG = {
  drogue: { radius: 0.3, color: '#ff8844', lineColor: '#cc6633', height: 0.6 },
  main: { radius: 0.55, color: '#e0e0e0', lineColor: '#aaaaaa', height: 0.9 },
};

const LINE_ANGLES = [0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2];

export function Parachute({ type }: ParachuteProps) {
  const groupRef = useRef<THREE.Group>(null!);
  const scaleRef = useRef(0);

  useFrame((_, delta) => {
    if (!groupRef.current) return;
    const target = type ? 1 : 0;
    scaleRef.current = THREE.MathUtils.lerp(scaleRef.current, target, delta * 4);
    groupRef.current.scale.setScalar(scaleRef.current);
    groupRef.current.visible = scaleRef.current > 0.01;
  });

  if (!type) return null;

  const config = CHUTE_CONFIG[type];

  return (
    <group ref={groupRef} position={[0, 1.6 + config.height, 0]}>
      {/* Canopy dome — open side faces down like a real parachute */}
      <mesh>
        <sphereGeometry args={[config.radius, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2]} />
        <meshStandardMaterial
          color={config.color}
          side={THREE.DoubleSide}
          transparent
          opacity={0.7}
          metalness={0.1}
          roughness={0.8}
        />
      </mesh>

      {/* Suspension lines — from canopy rim down to rocket body */}
      {LINE_ANGLES.map((angle, i) => {
        const x = Math.sin(angle) * config.radius * 0.85;
        const z = Math.cos(angle) * config.radius * 0.85;
        return (
          <Line
            key={i}
            points={[[x, 0, z], [0, -(config.height + 0.3), 0]]}
            color={config.lineColor}
            lineWidth={1}
          />
        );
      })}
    </group>
  );
}
