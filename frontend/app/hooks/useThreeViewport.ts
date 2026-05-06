"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import { annotationColor, makeTextSprite, setTrajectoryLine } from "../lib/viewportHelpers";
import type { SceneData, TrajectoryPoint3D } from "../types";

const A_COLOR = 0xffff00;
const B_COLOR = 0xff44ff;

interface Props {
  sceneData: SceneData | null;
  frameIdx: number;
  followCam: boolean;
  highlightA: string;
  highlightB: string;
  trajectoryA: TrajectoryPoint3D[];
  trajectoryB: TrajectoryPoint3D[];
}

export function useThreeViewport({
  sceneData,
  frameIdx,
  followCam,
  highlightA,
  highlightB,
  trajectoryA,
  trajectoryB,
}: Props) {
  const mountRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const animFrameRef = useRef<number>(0);
  const egoMeshRef = useRef<THREE.Mesh | null>(null);
  const annGroupRef = useRef<THREE.Group | null>(null);
  const gridRef = useRef<THREE.GridHelper | null>(null);
  const circleARef = useRef<THREE.Line | null>(null);
  const circleBRef = useRef<THREE.Line | null>(null);
  const trajectoryARef = useRef<THREE.Line | null>(null);
  const trajectoryBRef = useRef<THREE.Line | null>(null);
  const centerLineMatRef = useRef<LineMaterial | null>(null);

  useEffect(() => {
    if (!mountRef.current) return;
    const w = mountRef.current.clientWidth;
    const h = mountRef.current.clientHeight;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0a0a);
    sceneRef.current = scene;

    const camera = new THREE.PerspectiveCamera(60, w / h, 0.1, 10000);
    camera.up.set(0, 0, 1);
    camera.position.set(0, -80, 80);
    camera.lookAt(0, 0, 0);
    cameraRef.current = camera;

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(w, h);
    renderer.setPixelRatio(window.devicePixelRatio);
    mountRef.current.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controlsRef.current = controls;

    const grid = new THREE.GridHelper(4000, 400, 0x1a1a1a, 0x1a1a1a);
    grid.rotation.x = Math.PI / 2;
    scene.add(grid);
    gridRef.current = grid;

    scene.add(new THREE.AmbientLight(0xffffff, 0.6));

    const egoMesh = new THREE.Mesh(
      new THREE.BoxGeometry(4.5, 2, 1.6),
      new THREE.MeshStandardMaterial({ color: 0x00e5ff }),
    );
    egoMesh.visible = false;
    scene.add(egoMesh);
    egoMeshRef.current = egoMesh;

    const annGroup = new THREE.Group();
    scene.add(annGroup);
    annGroupRef.current = annGroup;

    const circlePts: THREE.Vector3[] = [];
    for (let i = 0; i <= 64; i++) {
      const a = (i / 64) * Math.PI * 2;
      circlePts.push(new THREE.Vector3(Math.cos(a), Math.sin(a), 0));
    }
    const circleGeo = new THREE.BufferGeometry().setFromPoints(circlePts);

    const circleA = new THREE.Line(circleGeo, new THREE.LineBasicMaterial({ color: A_COLOR }));
    circleA.visible = false;
    scene.add(circleA);
    circleARef.current = circleA;

    const circleB = new THREE.Line(circleGeo.clone(), new THREE.LineBasicMaterial({ color: B_COLOR }));
    circleB.visible = false;
    scene.add(circleB);
    circleBRef.current = circleB;

    const trajA = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: A_COLOR }));
    trajA.visible = false;
    scene.add(trajA);
    trajectoryARef.current = trajA;

    const trajB = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: B_COLOR }));
    trajB.visible = false;
    scene.add(trajB);
    trajectoryBRef.current = trajB;

    const animate = () => {
      animFrameRef.current = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    const onResize = () => {
      if (!mountRef.current) return;
      const nw = mountRef.current.clientWidth;
      const nh = mountRef.current.clientHeight;
      camera.aspect = nw / nh;
      camera.updateProjectionMatrix();
      renderer.setSize(nw, nh);
      if (centerLineMatRef.current) centerLineMatRef.current.resolution.set(nw, nh);
    };
    window.addEventListener("resize", onResize);
    const ro = new ResizeObserver(onResize);
    ro.observe(mountRef.current);

    return () => {
      window.removeEventListener("resize", onResize);
      ro.disconnect();
      cancelAnimationFrame(animFrameRef.current);
      controls.dispose();
      renderer.dispose();
      mountRef.current?.removeChild(renderer.domElement); // eslint-disable-line react-hooks/exhaustive-deps
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene || !sceneData) return;

    scene.children.filter((c) => c.userData.isMap).forEach((c) => scene.remove(c));

    const mapGroup = new THREE.Group();
    mapGroup.userData.isMap = true;
    const centerMat = new THREE.LineBasicMaterial({ color: 0x22aa22, opacity: 0.75, transparent: true });
    for (const polyline of sceneData.centerlines ?? []) {
      const pts = polyline.map(([x, y]) => new THREE.Vector3(x, y, 0.1));
      mapGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), centerMat));
    }
    scene.add(mapGroup);

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const f of sceneData.frames) {
      minX = Math.min(minX, f.ego.x); maxX = Math.max(maxX, f.ego.x);
      minY = Math.min(minY, f.ego.y); maxY = Math.max(maxY, f.ego.y);
      for (const a of f.annotations) {
        minX = Math.min(minX, a.x); maxX = Math.max(maxX, a.x);
        minY = Math.min(minY, a.y); maxY = Math.max(maxY, a.y);
      }
    }

    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    if (gridRef.current) gridRef.current.position.set(cx, cy, -0.05);

    const span = Math.max(maxX - minX, maxY - minY, 100);
    const camera = cameraRef.current!;
    camera.position.set(cx, cy - span * 0.4, span * 0.5);
    camera.lookAt(cx, cy, 0);
    controlsRef.current!.target.set(cx, cy, 0);
    controlsRef.current!.update();
  }, [sceneData]);

  useEffect(() => {
    if (!sceneData || !egoMeshRef.current || !annGroupRef.current) return;
    const frame = sceneData.frames[frameIdx];
    if (!frame) return;

    const { ego } = frame;
    egoMeshRef.current.position.set(ego.x, ego.y, ego.z + 0.8);
    egoMeshRef.current.quaternion.set(ego.qx, ego.qy, ego.qz, ego.qw);
    egoMeshRef.current.visible = true;

    const pinA = highlightA.trim();
    const pinB = highlightB.trim();
    const anyPin = pinA || pinB;

    const egoMat = egoMeshRef.current.material as THREE.MeshStandardMaterial;
    if (pinA && "ego".startsWith(pinA)) egoMat.color.setHex(A_COLOR);
    else if (pinB && "ego".startsWith(pinB)) egoMat.color.setHex(B_COLOR);
    else if (anyPin) { egoMat.color.setHex(0x00e5ff); egoMat.opacity = 0.15; egoMat.transparent = true; }
    else { egoMat.color.setHex(0x00e5ff); egoMat.opacity = 1; egoMat.transparent = false; }

    if (followCam && cameraRef.current && controlsRef.current) {
      const q = new THREE.Quaternion(ego.qx, ego.qy, ego.qz, ego.qw);
      const forward = new THREE.Vector3(1, 0, 0).applyQuaternion(q);
      const offset = forward.clone().multiplyScalar(-30).add(new THREE.Vector3(0, 0, 20));
      cameraRef.current.position.copy(new THREE.Vector3(ego.x, ego.y, ego.z).add(offset));
      controlsRef.current.target.set(ego.x, ego.y, ego.z + 1);
      controlsRef.current.update();
    }

    const group = annGroupRef.current;
    group.clear();
    if (circleARef.current) circleARef.current.visible = false;
    if (circleBRef.current) circleBRef.current.visible = false;

    const egoIsA = !!pinA && "ego".startsWith(pinA);
    const egoIsB = !!pinB && "ego".startsWith(pinB);
    const egoHighlighted = egoIsA || egoIsB;
    const egoLabelColor = egoIsA ? A_COLOR : egoIsB ? B_COLOR : 0x00e5ff;

    const egoSprite = makeTextSprite("ego", egoLabelColor);
    egoSprite.scale.set(egoHighlighted ? 7 : 5, egoHighlighted ? 2.5 : 1.75, 1);
    egoSprite.position.set(ego.x, ego.y, ego.z + 3.1);
    group.add(egoSprite);

    const egoR = Math.sqrt((4.5 / 2) ** 2 + (2 / 2) ** 2) * 1.3;
    if (egoIsA && circleARef.current) {
      circleARef.current.scale.set(egoR, egoR, 1);
      circleARef.current.position.set(ego.x, ego.y, ego.z + 0.1);
      circleARef.current.visible = true;
    }
    if (egoIsB && circleBRef.current) {
      circleBRef.current.scale.set(egoR, egoR, 1);
      circleBRef.current.position.set(ego.x, ego.y, ego.z + 0.1);
      circleBRef.current.visible = true;
    }

    for (const ann of frame.annotations) {
      if (!ann.cat.startsWith("vehicle.") && !ann.cat.startsWith("human.pedestrian")) continue;

      const isA = !!pinA && ann.token.startsWith(pinA);
      const isB = !!pinB && ann.token.startsWith(pinB);
      const isHighlighted = isA || isB;
      const color = isA ? A_COLOR : isB ? B_COLOR : annotationColor(ann.cat);
      const opacity = anyPin && !isHighlighted ? 0.15 : 1;

      const box = new THREE.LineSegments(
        new THREE.EdgesGeometry(new THREE.BoxGeometry(ann.l, ann.w, ann.h)),
        new THREE.LineBasicMaterial({ color, opacity, transparent: opacity < 1 }),
      );
      box.position.set(ann.x, ann.y, ann.z);
      box.quaternion.set(ann.qx, ann.qy, ann.qz, ann.qw);
      group.add(box);

      const r = Math.sqrt((ann.l / 2) ** 2 + (ann.w / 2) ** 2) * 1.3;
      if (isA && circleARef.current) {
        circleARef.current.scale.set(r, r, 1);
        circleARef.current.position.set(ann.x, ann.y, ann.z - ann.h / 2 + 0.1);
        circleARef.current.visible = true;
      }
      if (isB && circleBRef.current) {
        circleBRef.current.scale.set(r, r, 1);
        circleBRef.current.position.set(ann.x, ann.y, ann.z - ann.h / 2 + 0.1);
        circleBRef.current.visible = true;
      }

      const sprite = makeTextSprite(ann.token.slice(0, 5), color);
      sprite.scale.set(isHighlighted ? 7 : 5, isHighlighted ? 2.5 : 1.75, 1);
      sprite.position.set(ann.x, ann.y, ann.z + ann.h / 2 + 1.5);
      group.add(sprite);
    }
  }, [frameIdx, sceneData, followCam, highlightA, highlightB]);

  useEffect(() => {
    setTrajectoryLine(trajectoryARef.current, trajectoryA, A_COLOR);
    setTrajectoryLine(trajectoryBRef.current, trajectoryB, B_COLOR);
  }, [trajectoryA, trajectoryB]);

  return mountRef;
}
