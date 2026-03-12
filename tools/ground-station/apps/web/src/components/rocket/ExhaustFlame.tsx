import React, { useRef, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

interface ExhaustFlameProps {
  active: boolean;
  intensity: number; // 0-1 based on velocity
}

const PARTICLE_COUNT = 50;

export function ExhaustFlame({ active, intensity }: ExhaustFlameProps) {
  const meshRef = useRef<THREE.InstancedMesh>(null!);
  const dummy = useMemo(() => new THREE.Object3D(), []);
  const particleData = useRef(
    Array.from({ length: PARTICLE_COUNT }, () => ({
      y: Math.random() * -1.2,
      speed: 0.8 + Math.random() * 2.0,
      scale: 0.04 + Math.random() * 0.06,
      offset: Math.random() * Math.PI * 2,
    }))
  );

  useFrame((state, delta) => {
    if (!meshRef.current || !active) {
      if (meshRef.current) meshRef.current.visible = false;
      return;
    }
    meshRef.current.visible = true;

    const time = state.clock.elapsedTime;
    const particles = particleData.current;

    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const p = particles[i];
      p.y -= p.speed * delta * 4;
      if (p.y < -1.2) {
        p.y = -0.12;
        p.speed = 0.8 + Math.random() * 2.0;
      }

      const age = Math.abs(p.y) / 1.2; // 0 at engine, 1 at far
      // Bigger particles, scale with intensity
      const s = p.scale * (1.5 + age * 3) * (0.5 + intensity * 0.5);
      const wobble = Math.sin(time * 12 + p.offset) * 0.03 * (1 + age);

      dummy.position.set(wobble, p.y, wobble * 0.7);
      dummy.scale.set(s, s, s);
      dummy.updateMatrix();
      meshRef.current.setMatrixAt(i, dummy.matrix);

      // Bright orange core → red → fading
      const color = new THREE.Color();
      const hue = 0.08 - age * 0.06;
      const lightness = 0.7 - age * 0.35;
      color.setHSL(hue, 1, Math.max(0.1, lightness));
      meshRef.current.setColorAt(i, color);
    }

    meshRef.current.instanceMatrix.needsUpdate = true;
    if (meshRef.current.instanceColor) meshRef.current.instanceColor.needsUpdate = true;
  });

  return (
    <>
      <instancedMesh ref={meshRef} args={[undefined, undefined, PARTICLE_COUNT]}>
        <sphereGeometry args={[1, 8, 8]} />
        <meshBasicMaterial transparent opacity={0.9} toneMapped={false} />
      </instancedMesh>
      {/* Bright emissive core glow */}
      {active && (
        <mesh position={[0, -0.15, 0]}>
          <sphereGeometry args={[0.06 + intensity * 0.04, 8, 8]} />
          <meshBasicMaterial color="#ffaa33" transparent opacity={0.8 * intensity} toneMapped={false} />
        </mesh>
      )}
    </>
  );
}
