"use client";
import React, { useRef, useEffect, useState, useCallback } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { useRouter } from 'next/navigation'; // Kept for now, might be replaced by onMarkerClick

// Define GeoJSON Interfaces (assuming these are still used for continent outlines)
interface GeoJSONFeature {
    type: 'Feature';
    geometry: { type: 'Polygon'; coordinates: number[][][] } | { type: 'MultiPolygon'; coordinates: number[][][][] };
    properties: Record<string, unknown>;
}

interface GeoJSON {
    type: 'FeatureCollection';
    features: GeoJSONFeature[];
}

// Define GlobeDataPoint Interface for props
interface GlobeDataPoint {
  id: string;
  name: string;
  lat: number;
  lon: number;
  altitude?: number;
  status?: string;
  type?: string;
  [key: string]: any;
}

// Props for the ThreeGrid component
interface ThreeGridProps {
  dataPoints: GlobeDataPoint[];
  onMarkerClick?: (dataPoint: GlobeDataPoint) => void;
  // className?: string; // Example: if you need to pass Tailwind classes for sizing
  // style?: React.CSSProperties; // Example: if you need to pass inline styles for sizing
}
export type { GlobeDataPoint }; // Export the interface

// Internal FeedMarker Interface (for Three.js objects)
interface FeedMarker {
    id: string;
    name: string;
    position: THREE.Vector3;
    mesh?: THREE.Mesh<THREE.ConeGeometry, THREE.MeshBasicMaterial>;
    label?: THREE.Sprite;
    status: 'error' | 'stopped' | 'running' | 'starting'; // Keep this for status mapping
    // originalData: GlobeDataPoint; // Optional: store the original data point
}

// Structure to hold the scene, camera, renderer and controls
interface SceneRefs {
    scene: THREE.Scene;
    camera: THREE.PerspectiveCamera;
    renderer: THREE.WebGLRenderer;
    controls: OrbitControls;
    animationId: number | null;
}

// Helper to create label sprites (remains themed)
const createLabelSprite = (name: string, position: THREE.Vector3, offsetAmount: number = 5): THREE.Sprite | undefined => {
    try {
        const canvas = document.createElement('canvas');
        const context = canvas.getContext('2d');
        if (!context) return undefined;

        const fontSize = 16;
        const padding = 8;
        context.font = `Bold ${fontSize}px 'IBM Plex Mono', monospace`;
        const textMetrics = context.measureText(name);
        const textWidth = textMetrics.width;

        canvas.width = textWidth + padding * 2;
        canvas.height = fontSize + padding * 2;

        context.font = `Bold ${fontSize}px 'IBM Plex Mono', monospace`;
        context.fillStyle = 'rgba(0, 0, 0, 1)'; // Solid black background
        context.fillRect(0, 0, canvas.width, canvas.height);

        context.textAlign = 'center';
        context.textBaseline = 'middle';
        context.fillStyle = '#8CA17C'; // Theme green text
        context.fillText(name, canvas.width / 2, canvas.height / 2);

        const texture = new THREE.CanvasTexture(canvas);
        texture.needsUpdate = true;
        const spriteMaterial = new THREE.SpriteMaterial({
            map: texture,
            transparent: false,
            opacity: 1,
            sizeAttenuation: false
        });
        const label = new THREE.Sprite(spriteMaterial);
        
        label.scale.set(0.025 * canvas.width, 0.025 * canvas.height, 1.0);
        label.position.set(position.x, position.y + offsetAmount, position.z);
        label.userData = { name: name, isLabel: true };
        return label;
    } catch (labelError) {
        console.error("Error creating label canvas/texture:", labelError);
        return undefined;
    }
};


const ThreeGrid: React.FC<ThreeGridProps> = ({ dataPoints, onMarkerClick }) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const [feedMarkers, setFeedMarkers] = useState<Record<string, FeedMarker>>({});
    const router = useRouter(); // Kept for existing click and search logic
    const sceneRef = useRef<SceneRefs | null>(null);
    const globeRef = useRef<THREE.Mesh | null>(null);
    const feedMarkersRef = useRef(feedMarkers);

    useEffect(() => {
        feedMarkersRef.current = feedMarkers;
    }, [feedMarkers]);

    const isValidGeoJSONFeature = useCallback((f: unknown): f is GeoJSONFeature => {
        if (typeof f !== 'object' || f === null) return false;
        const feature = f as GeoJSONFeature;
        return (
            feature.type === 'Feature' &&
            typeof feature.geometry === 'object' &&
            (feature.geometry.type === 'Polygon' || feature.geometry.type === 'MultiPolygon') &&
            Array.isArray(feature.geometry.coordinates)
        );
    }, []);

    const lonLatToVector3 = useCallback((lon: number, lat: number, radius: number = 50.1): THREE.Vector3 => {
        const phi = (90 - lat) * (Math.PI / 180);
        const theta = (lon + 180) * (Math.PI / 180);
        return new THREE.Vector3(
            -radius * Math.sin(phi) * Math.cos(theta),
            radius * Math.cos(phi),
            radius * Math.sin(phi) * Math.sin(theta)
        );
    }, []);

    const latLonAltToVector3 = useCallback((lat: number, lon: number, alt: number = 0, radius: number = 50): THREE.Vector3 => {
        const phi = (90 - lat) * Math.PI / 180;
        const theta = (lon + 180) * Math.PI / 180;
        const r = radius + alt;
        return new THREE.Vector3(-(r * Math.sin(phi) * Math.cos(theta)), r * Math.cos(phi), r * Math.sin(phi) * Math.sin(theta));
    }, []);

    const addPolygonToScene = useCallback((polygonCoords: number[][][], scene: THREE.Scene, color: THREE.ColorRepresentation = 0x000000) => {
        polygonCoords.forEach((ringCoords: number[][]) => {
            if (!Array.isArray(ringCoords) || ringCoords.length < 3 || !Array.isArray(ringCoords[0]) || ringCoords[0].length !== 2) {
                return;
            }
            const points = ringCoords.map((coord: number[]) => {
                if (Array.isArray(coord) && coord.length === 2 && typeof coord[0] === 'number' && typeof coord[1] === 'number') {
                    return lonLatToVector3(coord[0], coord[1]);
                }
                console.warn("Skipping invalid coordinate pair in GeoJSON:", coord);
                return null;
            }).filter((p): p is THREE.Vector3 => p !== null);

            if (points.length > 1) {
                if (points[0].distanceToSquared(points[points.length - 1]) > 0.0001) {
                    points.push(points[0].clone());
                }
                const geometry = new THREE.BufferGeometry().setFromPoints(points);
                const material = new THREE.LineBasicMaterial({ color: color, opacity: 1, transparent: false, linewidth: 1 });
                const line = new THREE.Line(geometry, material);
                line.userData.isGeoJsonLine = true;
                scene.add(line);
            }
        });
    }, [lonLatToVector3]);

    const loadGeoJSON = useCallback(async (scene: THREE.Scene): Promise<void> => {
        try {
            const response = await fetch('/continents.geojson');
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            const geojson: GeoJSON = await response.json();
            if (geojson.type !== 'FeatureCollection' || !Array.isArray(geojson.features)) {
                 console.error('Invalid GeoJSON format.');
                 return;
            }
            geojson.features.forEach((feature: unknown, index: number) => {
                if (!isValidGeoJSONFeature(feature)) {
                    return;
                }
                try {
                    if (feature.geometry.type === 'Polygon') {
                        addPolygonToScene(feature.geometry.coordinates, scene);
                    } else if (feature.geometry.type === 'MultiPolygon') {
                        feature.geometry.coordinates.forEach((polygonCoords: number[][][]) => {
                            addPolygonToScene(polygonCoords, scene);
                        });
                    }
                } catch (processingError) {
                    console.error(`Error processing GeoJSON feature ${index}:`, processingError, feature);
                }
            });
        } catch (error) {
            console.error('Failed to load or parse GeoJSON:', error);
        }
    }, [addPolygonToScene, isValidGeoJSONFeature]);

    useEffect(() => {
        const currentContainer = containerRef.current;
        if (!currentContainer || sceneRef.current) return;

        const scene = new THREE.Scene();
        scene.fog = new THREE.Fog(0x8CA17C, 80, 250); // Theme green fog
        const camera = new THREE.PerspectiveCamera(75, currentContainer.clientWidth / currentContainer.clientHeight, 0.1, 1000);
        const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setClearColor(0x000000, 0);
        renderer.setSize(currentContainer.clientWidth, currentContainer.clientHeight);
        renderer.setPixelRatio(window.devicePixelRatio);
        currentContainer.appendChild(renderer.domElement);

        const globeGeometry = new THREE.SphereGeometry(50, 64, 64);
        const globeMaterial = new THREE.MeshBasicMaterial({
            color: 0x8CA17C, wireframe: false, opacity: 1, transparent: false,
        });
        const globe = new THREE.Mesh(globeGeometry, globeMaterial);
        globeRef.current = globe;
        scene.add(globe);

        loadGeoJSON(scene);

        const controls = new OrbitControls(camera, renderer.domElement);
        controls.enableDamping = true; controls.dampingFactor = 0.05;
        controls.minDistance = 60; controls.maxDistance = 300;
        controls.enablePan = false;

        camera.position.set(0, 0, 120);
        camera.lookAt(scene.position);
        controls.update();

        sceneRef.current = { scene, camera, renderer, controls, animationId: null };

        const handleResize = () => {
            if (!containerRef.current || !sceneRef.current) return;
            const { camera: cam, renderer: rend } = sceneRef.current;
            const width = containerRef.current.clientWidth;
            const height = containerRef.current.clientHeight;
            cam.aspect = width / height;
            cam.updateProjectionMatrix();
            rend.setSize(width, height);
        };
        window.addEventListener('resize', handleResize);

        const animateScene = () => {
            const currentSRefs = sceneRef.current;
            if (!currentSRefs) return;
            const currentMarkers = feedMarkersRef.current;

            Object.values(currentMarkers).forEach(marker => {
                if (marker.mesh) {
                    marker.mesh.rotation.y += 0.01;
                    const material = marker.mesh.material as THREE.MeshBasicMaterial;
                    const labelMaterial = marker.label?.material as THREE.SpriteMaterial | undefined;

                    if (marker.status === 'running') {
                        const pulseFactor = 0.8 + Math.sin(Date.now() * 0.005) * 0.2;
                        material.opacity = pulseFactor;
                        if (labelMaterial) labelMaterial.opacity = pulseFactor;
                    } else {
                        material.opacity = 0.8;
                        if (labelMaterial) labelMaterial.opacity = 0.8;
                    }
                }
            });

            currentSRefs.controls.update();
            currentSRefs.renderer.render(currentSRefs.scene, currentSRefs.camera);
            currentSRefs.animationId = requestAnimationFrame(animateScene);
        };
        animateScene();

        return () => {
            const sRefsToClean = sceneRef.current;
            if (!sRefsToClean) return;
            window.removeEventListener('resize', handleResize);
            if (sRefsToClean.animationId) cancelAnimationFrame(sRefsToClean.animationId);
            sRefsToClean.controls?.dispose();
            sRefsToClean.scene?.traverse((object) => {
                if (object instanceof THREE.Mesh || object instanceof THREE.Line || object instanceof THREE.Sprite) {
                    object.geometry?.dispose();
                    if (object.material) {
                        if (Array.isArray(object.material)) {
                            object.material.forEach(mat => { mat.map?.dispose(); mat.dispose(); });
                        } else {
                            object.material.map?.dispose();
                            object.material.dispose();
                        }
                    }
                }
            });
            while(sRefsToClean.scene?.children.length > 0){
                sRefsToClean.scene.remove(sRefsToClean.scene.children[0]);
            }
            if (currentContainer && sRefsToClean.renderer?.domElement) {
                 if (currentContainer.contains(sRefsToClean.renderer.domElement)) {
                    currentContainer.removeChild(sRefsToClean.renderer.domElement);
                 }
            }
            sRefsToClean.renderer?.dispose();
            sceneRef.current = null;
            globeRef.current = null;
            feedMarkersRef.current = {}; // Reset ref
            setFeedMarkers({}); // Reset state
        };
    }, [loadGeoJSON]); // loadGeoJSON is stable

    const mapToMarkerStatus = (statusStr: string | undefined): FeedMarker['status'] => {
        if (statusStr && ['error', 'stopped', 'running', 'starting'].includes(statusStr)) {
            return statusStr as FeedMarker['status'];
        }
        return 'stopped';
    };

    useEffect(() => {
        const currentSceneRefs = sceneRef.current;
        if (!currentSceneRefs) return;
        const { scene } = currentSceneRefs;

        const MARKER_BASE_COLOR = 0x000000; // Black
        const MARKER_RUNNING_COLOR = 0x8CA17C; // Theme green

        setFeedMarkers(prevMarkers => {
            const updatedMarkers: Record<string, FeedMarker> = { ...prevMarkers };
            const incomingPointIds = new Set(dataPoints.map(p => p.id));
            const existingMarkerIds = new Set(Object.keys(prevMarkers));

            dataPoints.forEach(point => {
                const { id, name, lat, lon, altitude = 1, status } = point;
                const position = latLonAltToVector3(lat, lon, altitude, 50);
                const markerStatus = mapToMarkerStatus(status);
                const newThemeMarkerColor = markerStatus === 'running' ? MARKER_RUNNING_COLOR : MARKER_BASE_COLOR;

                const existingMarker = updatedMarkers[id];
                if (existingMarker) {
                    existingMarker.position.copy(position);
                    existingMarker.status = markerStatus;
                    existingMarker.name = name;

                    if (existingMarker.mesh) {
                        (existingMarker.mesh.material as THREE.MeshBasicMaterial).color.setHex(newThemeMarkerColor);
                        existingMarker.mesh.position.copy(position);
                        existingMarker.mesh.lookAt(0, 0, 0);
                        existingMarker.mesh.userData = { ...point, isMarker: true };
                    }
                    if (existingMarker.label) { // Simple remove and re-add for labels if name or position changed
                        if (existingMarker.name !== name || !existingMarker.label.position.equals(position)) {
                            scene.remove(existingMarker.label);
                            existingMarker.label.material.map?.dispose();
                            existingMarker.label.material.dispose();
                            existingMarker.label = createLabelSprite(name, position) || undefined;
                            if (existingMarker.label) scene.add(existingMarker.label);
                        } else { // Just update position if only that changed
                             const labelOffset = 5; // Assuming same offset as in createLabelSprite
                             existingMarker.label.position.set(position.x, position.y + labelOffset, position.z);
                        }
                    }
                } else {
                    const newMarker: FeedMarker = { id, name, position, status: markerStatus };
                    const geometry = new THREE.ConeGeometry(0.8, 3, 8);
                    geometry.translate(0, 1.5, 0);
                    geometry.rotateX(Math.PI / 2);
                    const material = new THREE.MeshBasicMaterial({ color: newThemeMarkerColor, transparent: true, opacity: 0.8 });
                    const mesh = new THREE.Mesh(geometry, material);
                    mesh.position.copy(position);
                    mesh.lookAt(new THREE.Vector3(0,0,0));
                    mesh.userData = { ...point, isMarker: true };
                    newMarker.mesh = mesh;
                    scene.add(mesh);

                    newMarker.label = createLabelSprite(name, position) || undefined;
                    if (newMarker.label) scene.add(newMarker.label);
                    updatedMarkers[id] = newMarker;
                }
            });

            existingMarkerIds.forEach(markerId => {
                if (!incomingPointIds.has(markerId)) {
                    const markerToRemove = updatedMarkers[markerId];
                    if (markerToRemove.mesh) {
                        scene.remove(markerToRemove.mesh);
                        markerToRemove.mesh.geometry.dispose();
                       (markerToRemove.mesh.material as THREE.Material).dispose();
                    }
                    if (markerToRemove.label) {
                        scene.remove(markerToRemove.label);
                        markerToRemove.label.material.map?.dispose();
                        markerToRemove.label.material.dispose();
                    }
                    delete updatedMarkers[markerId];
                }
            });
            return updatedMarkers;
        });

    }, [dataPoints, latLonAltToVector3]);


    useEffect(() => {
        const currentContainer = containerRef.current;
        if (!currentContainer) return;

        const raycaster = new THREE.Raycaster();
        const mouse = new THREE.Vector2();

        const handleClick = (event: MouseEvent): void => {
            const currentSRefs = sceneRef.current;
            if (!currentContainer || !currentSRefs) return;
            const { camera } = currentSRefs;
            const currentMarkers = feedMarkersRef.current; // Use ref for up-to-date markers

            const rect = currentContainer.getBoundingClientRect();
            mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
            mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
            raycaster.setFromCamera(mouse, camera);

            const markerMeshes = Object.values(currentMarkers)
                .map(f => f.mesh)
                .filter((m): m is THREE.Mesh<THREE.ConeGeometry, THREE.MeshBasicMaterial> => !!m);

            if (markerMeshes.length > 0) {
                const intersects = raycaster.intersectObjects(markerMeshes);
                if (intersects.length > 0) {
                    const intersectedObject = intersects[0].object;
                    // Use intersectedObject.userData which now holds the GlobeDataPoint
                    if (onMarkerClick && intersectedObject.userData?.id) {
                        onMarkerClick(intersectedObject.userData as GlobeDataPoint);
                    } else if (intersectedObject.userData?.id) { // Fallback if onMarkerClick not provided
                        router.push(`/surveillance/${intersectedObject.userData.id}`);
                    }
                    return;
                }
            }
            // ... (globe click logic can remain if needed)
        };
        currentContainer.addEventListener('click', handleClick);
        return () => {
            currentContainer.removeEventListener('click', handleClick);
        };
    }, [router, onMarkerClick]); // Add onMarkerClick to dependencies


    const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const currentSceneRefs = sceneRef.current;
        if (!currentSceneRefs) return;
        const { camera, controls } = currentSceneRefs;

        const searchTermValue = e.target.value.toLowerCase().trim();
        if (!searchTermValue) {
            controls.target.set(0,0,0);
            camera.position.set(0,0,120); // Reset camera
            controls.update();
            return;
        }

        // Search directly on the dataPoints prop
        const matchedPoint = dataPoints.find(
            (point: GlobeDataPoint) =>
                point.name.toLowerCase().includes(searchTermValue) ||
                point.id.toLowerCase().includes(searchTermValue)
        );

        if (matchedPoint) {
            const currentMarkers = feedMarkersRef.current; // Get current markers state
            const actualMarker = currentMarkers[matchedPoint.id];

            if (actualMarker?.position) { // Check if the marker exists and has a position
                const offsetDistance = 20;
                const directionToMarker = actualMarker.position.clone().normalize();
                const desiredCameraPosition = actualMarker.position.clone().add(directionToMarker.multiplyScalar(offsetDistance));

                const minFocusDistance = 50 + 15; // Keep existing logic
                if (desiredCameraPosition.length() < minFocusDistance) {
                    desiredCameraPosition.normalize().multiplyScalar(minFocusDistance);
                }

                controls.target.copy(actualMarker.position);
                camera.position.copy(desiredCameraPosition);
                controls.update();
            }
        }
    };

    return (
        <div className="relative w-full h-[600px] overflow-hidden bg-background cursor-grab active:cursor-grabbing">
            <div ref={containerRef} className="absolute inset-0 w-full h-full" />
            <div className="absolute top-4 right-4 z-10 bg-primary p-3 border border-primary-foreground">
                <input
                    type="text"
                    placeholder="Search feed name/ID..."
                    className="bg-transparent text-primary-foreground border-none focus:outline-none px-2 py-1 w-60 placeholder-primary-foreground text-sm tracking-normal"
                    onChange={handleSearchChange}
                />
            </div>
        </div>
    );
};

export default ThreeGrid;