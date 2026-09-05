import { Suspense, useRef } from "react";
import { Canvas } from "@react-three/fiber";
import { Float, Sparkles } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

/**
 * 3D 设备核心（React-Three-Fiber）：线框二十面体 + 双旋转环 + 粒子云。
 * useFrame 驱动的渲染循环；由 React.lazy + Suspense 按需加载。
 */

function Core() {
  const core = useRef<THREE.Mesh>(null);
  const ringA = useRef<THREE.Mesh>(null);
  const ringB = useRef<THREE.Mesh>(null);

  useFrame((state, delta) => {
    if (core.current) {
      core.current.rotation.y += delta * 0.25;
      core.current.rotation.x += delta * 0.08;
    }
    if (ringA.current) ringA.current.rotation.z += delta * 0.6;
    if (ringB.current) {
      ringB.current.rotation.z -= delta * 0.45;
      ringB.current.rotation.x = Math.sin(state.clock.elapsedTime * 0.4) * 0.35 + 1.1;
    }
  });

  return (
    <group>
      {/* 核心：实体暗核 + 外层线框 */}
      <mesh ref={core}>
        <icosahedronGeometry args={[1.15, 1]} />
        <meshStandardMaterial
          color="#0d1117"
          metalness={0.9}
          roughness={0.25}
          emissive="#12305c"
          emissiveIntensity={0.6}
        />
      </mesh>
      <mesh scale={1.28}>
        <icosahedronGeometry args={[1.15, 1]} />
        <meshBasicMaterial color="#4f8ff7" wireframe transparent opacity={0.35} />
      </mesh>

      {/* 陀螺仪式双环 */}
      <mesh ref={ringA} rotation={[1.2, 0, 0]}>
        <torusGeometry args={[2.0, 0.015, 8, 128]} />
        <meshBasicMaterial color="#4f8ff7" transparent opacity={0.7} />
      </mesh>
      <mesh ref={ringB} rotation={[1.1, 0, 0]}>
        <torusGeometry args={[2.45, 0.01, 8, 128]} />
        <meshBasicMaterial color="#8957e5" transparent opacity={0.55} />
      </mesh>

      {/* 数据粒子 */}
      <Sparkles count={130} scale={[7, 5, 7]} size={1.6} speed={0.35} color="#6ba1ff" />
    </group>
  );
}

export default function HeroScene() {
  return (
    <div className="hero-canvas" aria-hidden>
      <Canvas
        camera={{ position: [0, 0.6, 5.4], fov: 45 }}
        dpr={[1, 2]}
        gl={{ antialias: true, alpha: true }}
      >
        <ambientLight intensity={0.35} />
        <pointLight position={[4, 3, 5]} intensity={40} color="#4f8ff7" />
        <pointLight position={[-5, -2, -3]} intensity={25} color="#8957e5" />
        <Suspense fallback={null}>
          <Float speed={1.4} rotationIntensity={0.25} floatIntensity={0.9}>
            <Core />
          </Float>
        </Suspense>
      </Canvas>
    </div>
  );
}
