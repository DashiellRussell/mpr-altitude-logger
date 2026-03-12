import React, { useRef, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

interface RocketModelProps {
  state: string;
  velocity: number;
  altitude: number;
  maxAlt: number;
}

// States where the nosecone has separated
const NOSE_DETACHED_STATES = new Set(['APOGEE', 'DROGUE', 'MAIN', 'LANDED']);
// States where the nosecone should be fully hidden (gone)
const NOSE_GONE_STATES = new Set(['DROGUE', 'MAIN', 'LANDED']);

// Generate a triangular fin shape in the XY plane
// The shape lies flat — X is the span (outward from body), Y is height (along rocket axis)
function createFinShape(): THREE.Shape {
  const shape = new THREE.Shape();
  shape.moveTo(0, 0);          // root bottom (at body surface)
  shape.lineTo(0.15, 0);       // tip bottom (outward)
  shape.lineTo(0.04, 0.28);    // tip top
  shape.lineTo(0, 0.22);       // root top
  shape.closePath();
  return shape;
}

export function RocketModel({ state, velocity, altitude, maxAlt }: RocketModelProps) {
  const groupRef = useRef<THREE.Group>(null!);
  const noseRef = useRef<THREE.Group>(null!);
  const targetRotation = useRef(0);
  const noseSepProgress = useRef(0);
  const noseBaseY = 1.2; // nosecone resting Y (top of body tube)

  // Parabolic nosecone geometry — LatheGeometry from a curve
  const noseGeometry = useMemo(() => {
    const points: THREE.Vector2[] = [];
    const segments = 20;
    const height = 0.5;
    const radius = 0.12;
    for (let i = 0; i <= segments; i++) {
      const t = i / segments; // 0 = tip, 1 = base
      // Parabolic profile: r = radius * sqrt(t)
      const r = radius * Math.sqrt(t);
      const y = height * (1 - t);
      points.push(new THREE.Vector2(r, y));
    }
    return new THREE.LatheGeometry(points, 24);
  }, []);

  // Fin shape for extrusion
  const finGeometry = useMemo(() => {
    const shape = createFinShape();
    return new THREE.ExtrudeGeometry(shape, {
      depth: 0.004,
      bevelEnabled: false,
    });
  }, []);

  const tilt = useMemo(() => {
    switch (state) {
      case 'PAD': return 0;
      case 'BOOST': return 0.02;
      case 'COAST': return 0.1;
      case 'APOGEE': return 0.5;
      case 'DROGUE':
      case 'MAIN': return 0.05;
      case 'LANDED': return 0.08;
      default: return 0;
    }
  }, [state]);

  targetRotation.current = tilt;
  const noseDetached = NOSE_DETACHED_STATES.has(state);
  const noseGone = NOSE_GONE_STATES.has(state);

  useFrame((_, delta) => {
    if (!groupRef.current) return;

    // Smooth rotation
    groupRef.current.rotation.z = THREE.MathUtils.lerp(
      groupRef.current.rotation.z,
      targetRotation.current,
      delta * 2
    );

    // Very subtle vibration during BOOST
    if (state === 'BOOST') {
      groupRef.current.position.x = (Math.random() - 0.5) * 0.004;
      groupRef.current.position.z = (Math.random() - 0.5) * 0.004;
    } else {
      groupRef.current.position.x = THREE.MathUtils.lerp(groupRef.current.position.x, 0, delta * 5);
      groupRef.current.position.z = THREE.MathUtils.lerp(groupRef.current.position.z, 0, delta * 5);
    }

    // Nosecone separation animation
    if (noseRef.current) {
      const sepTarget = noseDetached ? 1 : 0;
      noseSepProgress.current = THREE.MathUtils.lerp(
        noseSepProgress.current, sepTarget, delta * 2.5
      );
      const p = noseSepProgress.current;

      // Once fully separated in later states, hide completely
      if (noseGone && p > 0.95) {
        noseRef.current.visible = false;
      } else {
        noseRef.current.visible = true;
        // Drift up and tumble sideways
        noseRef.current.position.set(
          p * 0.6,
          noseBaseY + p * 1.0,
          p * 0.3
        );
        noseRef.current.rotation.set(p * 1.0, 0, p * 2.0);
        const s = 1 - p * 0.6; // shrink more aggressively
        noseRef.current.scale.setScalar(Math.max(0.01, s));
      }
    }
  });

  return (
    <group ref={groupRef}>
      {/* Body tube */}
      <mesh position={[0, 0.6, 0]}>
        <cylinderGeometry args={[0.12, 0.12, 1.2, 16]} />
        <meshStandardMaterial color="#d0d0d0" metalness={0.3} roughness={0.6} />
      </mesh>

      {/* Nosecone — parabolic, detaches at apogee, disappears in later states */}
      <group ref={noseRef} position={[0, noseBaseY, 0]}>
        <mesh geometry={noseGeometry}>
          <meshStandardMaterial color="#e0e0e0" metalness={0.4} roughness={0.5} />
        </mesh>
      </group>

      {/* Fins (4x) — triangular, orange, pointing outward
           The fin shape is in XY: X = span outward, Y = height along rocket.
           We rotate it so Y aligns with rocket Y axis, and X points radially out (Z). */}
      {[0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2].map((angle, i) => (
        <group key={i} rotation={[0, angle, 0]}>
          <mesh
            geometry={finGeometry}
            position={[0, -0.02, 0.12]}
            rotation={[0, -Math.PI / 2, 0]}
          >
            <meshStandardMaterial color="#ff8800" metalness={0.3} roughness={0.5} />
          </mesh>
        </group>
      ))}

      {/* Engine bell */}
      <mesh position={[0, -0.05, 0]} rotation={[Math.PI, 0, 0]}>
        <coneGeometry args={[0.08, 0.15, 12]} />
        <meshStandardMaterial color="#444" metalness={0.5} roughness={0.4} />
      </mesh>
    </group>
  );
}
