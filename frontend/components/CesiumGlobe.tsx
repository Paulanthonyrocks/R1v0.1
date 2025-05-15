"use client";
import React, { useRef, useEffect, useState, useCallback } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { useRouter } from 'next/navigation';
import type { FeedStatusData } from '@/lib/types';
import { db } from '@/lib/firebase'; // Assuming db is a Firestore instance from Firebase v9+
import { collection, getDocs, QueryDocumentSnapshot, DocumentData } from 'firebase/firestore'; // Firebase v9+ imports

// Define GeoJSON Interfaces
interface GeoJSONFeature {
    type: 'Feature';
    geometry: { type: 'Polygon'; coordinates: number[][][] } | { type: 'MultiPolygon'; coordinates: number[][][][] };
    properties: Record<string, unknown>;
}

interface GeoJSON {
    type: 'FeatureCollection';
    features: GeoJSONFeature[];
}

// Define FeedMarker Interface
interface FeedMarker {
    id: string;
    name: string;
    position: THREE.Vector3;
    mesh?: THREE.Mesh<THREE.ConeGeometry, THREE.MeshBasicMaterial>;
    label?: THREE.Sprite; // THREE.Sprite | undefined
    status: 'error' | 'stopped' | 'running' | 'starting';
}

// Structure to hold the scene, camera, renderer and controls
interface SceneRefs {
    scene: THREE.Scene;
    camera: THREE.PerspectiveCamera;
    renderer: THREE.WebGLRenderer;
    controls: OrbitControls;
    animationId: number | null;
}

// Helper to create label sprites
const createLabelSprite = (name: string, position: THREE.Vector3, offsetAmount: number = 5): THREE.Sprite | undefined => {
    try {
        const canvas = document.createElement('canvas');
        const context = canvas.getContext('2d');
        if (!context) return undefined; // Return undefined instead of null

        const fontSize = 16;
        const padding = 8;
        context.font = `Bold ${fontSize}px monospace`;
        const textMetrics = context.measureText(name);
        const textWidth = textMetrics.width;

        canvas.width = textWidth + padding * 2;
        canvas.height = fontSize + padding * 2;

        context.font = `Bold ${fontSize}px monospace`;
        context.fillStyle = 'rgba(0, 0, 0, 0.7)';
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.textAlign = 'center';
        context.textBaseline = 'middle';
        context.fillStyle = '#FFFFFF';
        context.fillText(name, canvas.width / 2, canvas.height / 2);

        const texture = new THREE.CanvasTexture(canvas);
        texture.needsUpdate = true;
        const spriteMaterial = new THREE.SpriteMaterial({
            map: texture,
            transparent: true,
            opacity: 0.9,
            sizeAttenuation: false
        });
        const label = new THREE.Sprite(spriteMaterial);
        
        label.scale.set(0.025 * canvas.width, 0.025 * canvas.height, 1.0);
        label.position.set(position.x, position.y + offsetAmount, position.z);
        label.userData = { name: name, isLabel: true };
        return label;
    } catch (labelError) {
        console.error("Error creating label canvas/texture:", labelError);
        return undefined; // Return undefined instead of null
    }
};


const ThreeGrid: React.FC = () => {
    const containerRef = useRef<HTMLDivElement>(null);
    const [feedMarkers, setFeedMarkers] = useState<Record<string, FeedMarker>>({});
    const router = useRouter();
    const sceneRef = useRef<SceneRefs | null>(null);
    const globeRef = useRef<THREE.Mesh | null>(null);
    const feedMarkersRef = useRef(feedMarkers);

    useEffect(() => {
        feedMarkersRef.current = feedMarkers;
    }, [feedMarkers]);

    // Wrapped in useCallback as per ESLint suggestion
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

    const addPolygonToScene = useCallback((polygonCoords: number[][][], scene: THREE.Scene, color: THREE.ColorRepresentation = 0x00cc00) => {
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
                const material = new THREE.LineBasicMaterial({ color: color, opacity: 0.5, transparent: true, linewidth: 1 });
                const line = new THREE.Line(geometry, material);
                line.userData.isGeoJsonLine = true;
                scene.add(line);
            }
        });
    }, [lonLatToVector3]);

    const loadGeoJSON = useCallback(async (scene: THREE.Scene): Promise<void> => {
        console.log('Loading GeoJSON...');
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
                if (!isValidGeoJSONFeature(feature)) { // isValidGeoJSONFeature is now stable
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
            console.log('GeoJSON loaded successfully.');
        } catch (error) {
            console.error('Failed to load or parse GeoJSON:', error);
        }
    }, [addPolygonToScene, isValidGeoJSONFeature]); // Dependencies are stable

    useEffect(() => {
        const currentContainer = containerRef.current; // Capture ref for use in effect and cleanup
        if (!currentContainer || sceneRef.current) return;

        console.log("Initializing Three.js Scene");
        const scene = new THREE.Scene();
        scene.fog = new THREE.Fog(0x000000, 80, 250);
        const camera = new THREE.PerspectiveCamera(75, currentContainer.clientWidth / currentContainer.clientHeight, 0.1, 1000);
        const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setClearColor(0x000000, 0);
        renderer.setSize(currentContainer.clientWidth, currentContainer.clientHeight);
        renderer.setPixelRatio(window.devicePixelRatio);
        currentContainer.appendChild(renderer.domElement);

        const globeGeometry = new THREE.SphereGeometry(50, 64, 64);
        const globeMaterial = new THREE.MeshBasicMaterial({
            color: 0x001133, wireframe: true, opacity: 0.6, transparent: true,
        });
        const globe = new THREE.Mesh(globeGeometry, globeMaterial);
        globeRef.current = globe;
        scene.add(globe);

        loadGeoJSON(scene);

        const controls = new OrbitControls(camera, renderer.domElement);
        controls.enableDamping = true;
        controls.dampingFactor = 0.05;
        controls.minDistance = 60;
        controls.maxDistance = 300;
        controls.enablePan = false;

        camera.position.set(0, 0, 120);
        camera.lookAt(scene.position);
        controls.update();

        sceneRef.current = { scene, camera, renderer, controls, animationId: null };

        const handleResize = () => {
            // Use captured currentContainer for consistency if needed, though direct ref is fine here
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
            const currentSceneRefs = sceneRef.current;
            if (!currentSceneRefs) return;
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

            currentSceneRefs.controls.update();
            currentSceneRefs.renderer.render(currentSceneRefs.scene, currentSceneRefs.camera);
            currentSceneRefs.animationId = requestAnimationFrame(animateScene);
        };
        animateScene();

        return () => {
            console.log("Cleaning up Three.js Scene");
            const sceneRefsToClean = sceneRef.current;
            if (!sceneRefsToClean) return;

            window.removeEventListener('resize', handleResize);
            if (sceneRefsToClean.animationId) cancelAnimationFrame(sceneRefsToClean.animationId);
            
            sceneRefsToClean.controls?.dispose();
            sceneRefsToClean.scene?.traverse((object) => {
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
            while(sceneRefsToClean.scene?.children.length > 0){
                sceneRefsToClean.scene.remove(sceneRefsToClean.scene.children[0]);
            }
            // Use the captured 'currentContainer' in cleanup
            if (currentContainer && sceneRefsToClean.renderer?.domElement) {
                 if (currentContainer.contains(sceneRefsToClean.renderer.domElement)) {
                    currentContainer.removeChild(sceneRefsToClean.renderer.domElement);
                 }
            }
            sceneRefsToClean.renderer?.dispose();
            sceneRef.current = null;
            globeRef.current = null;
            feedMarkersRef.current = {};
            setFeedMarkers({});
        };
    }, [loadGeoJSON]);

    useEffect(() => {
        const currentContainer = containerRef.current; // Capture for cleanup
        if (!currentContainer) return;

        const raycaster = new THREE.Raycaster();
        const mouse = new THREE.Vector2();

        const handleClick = (event: MouseEvent): void => {
            const currentSceneRefs = sceneRef.current;
            // Use currentContainer for consistency within this effect's scope
            if (!currentContainer || !currentSceneRefs) return;
            const { camera } = currentSceneRefs;
            const currentMarkers = feedMarkersRef.current;

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
                    if (intersectedObject.userData?.id) {
                        router.push(`/surveillance/${intersectedObject.userData.id}`);
                    }
                    return;
                }
            }
            
            const currentGlobe = globeRef.current;
            if (currentGlobe) {
                const globeIntersects = raycaster.intersectObject(currentGlobe);
                if (globeIntersects.length > 0) { /* Clicked on globe */ }
            }
        };
        currentContainer.addEventListener('click', handleClick);
        return () => {
            // Use captured currentContainer for removeEventListener
            currentContainer.removeEventListener('click', handleClick);
        };
    }, [router]);

    const latLonAltToVector3 = useCallback((lat: number, lon: number, alt: number = 0, radius: number = 50): THREE.Vector3 => {
        const phi = (90 - lat) * Math.PI / 180;
        const theta = (lon + 180) * Math.PI / 180; // Longitude needs to be in [-180, 180] range for this typically
        const r = radius + alt;
        return new THREE.Vector3(-(r * Math.sin(phi) * Math.cos(theta)), r * Math.cos(phi), r * Math.sin(phi) * Math.sin(theta));
    }, []);

    useEffect(() => {
        const mapToMarkerStatus = (statusStr: string | undefined): FeedMarker['status'] => {
            if (statusStr && ['error', 'stopped', 'running', 'starting'].includes(statusStr)) {
                return statusStr as FeedMarker['status'];
            }
            return 'stopped';
        };

        const fetchAndUpdateFeeds = async () => {
            const currentSceneRefs = sceneRef.current;
            if (!currentSceneRefs) return;
            const { scene } = currentSceneRefs;

            if (!db) { // Check if db is initialized
                console.error("Firestore instance (db) is not initialized.");
                return;
            }

            let fetchedData: FeedStatusData[];
            try {
                // Firebase v9+ modular SDK syntax
                const feedsCollectionRef = collection(db, 'feeds'); // Ensure 'feeds' is your collection name
                const snapshot = await getDocs(feedsCollectionRef);
                fetchedData = snapshot.docs.map((doc: QueryDocumentSnapshot<DocumentData>) => ({ // Typed 'doc'
                    id: doc.id, 
                    ...doc.data() 
                } as FeedStatusData)); // Cast to FeedStatusData
                
                if (!Array.isArray(fetchedData)) {
                    console.error("Fetched data is not an array:", fetchedData);
                    fetchedData = [];
                }
            } catch (fetchError: unknown) { // Use unknown for error type
                let message = "An unknown error occurred";
                if (fetchError instanceof Error) {
                    message = fetchError.message;
                } else if (typeof fetchError === 'string') {
                    message = fetchError;
                }
                console.error('Error fetching or parsing feeds:', message);
                return;
            }

            setFeedMarkers(prevFeedMarkers => {
                const updatedMarkers: Record<string, FeedMarker> = { ...prevFeedMarkers };
                const incomingFeedIds = new Set(fetchedData.map((f: FeedStatusData) => f.id));
                const existingFeedIds = new Set(Object.keys(prevFeedMarkers));
                const totalFeeds = fetchedData.length > 0 ? fetchedData.length : 1;

                fetchedData.forEach((feed: FeedStatusData, index: number) => {
                    const existingMarker = updatedMarkers[feed.id];

                    // Corrected Fibonacci sphere distribution (points on a unit sphere)
                    const y_sphere = totalFeeds > 1 ? 1 - (index / (totalFeeds - 1)) * 2 : 0; // y from 1 to -1
                    const phi_golden_angle = index * (Math.PI * (3.0 - Math.sqrt(5.0))); // Golden angle for longitude distribution

                    // Convert spherical (unit sphere) to Cartesian, then to lat/lon
                    // x = r * cos(theta), z = r * sin(theta) for the slice
                    // For longitude, we use atan2(z_sphere_coord, x_sphere_coord)
                    // For latitude, we use asin(y_sphere_coord)
                    const lat = Math.asin(y_sphere) * (180 / Math.PI);
                    // Ensure phi_golden_angle is wrapped to [0, 2PI) before converting to degrees
                    // Then shift to [-180, 180] if needed, but lonLatToVector3 handles [0, 360] or [-180, 180] for longitude
                    let lon = (phi_golden_angle * (180 / Math.PI)) % 360;
                    if (lon > 180) lon -= 360; // Normalize to [-180, 180] if desired, though lonLatToVector3 can often handle larger ranges

                    const altitude = 1;
                    const position = latLonAltToVector3(lat, lon, altitude, 50);

                    const status = mapToMarkerStatus(feed.status);
                    const color = status === 'running' ? 0x00ff00 : (status === 'error' ? 0xff0000 : 0xcccccc);
                    const name = feed.name || feed.source || `Feed ${feed.id}`;

                    if (existingMarker) {
                        existingMarker.position.copy(position);
                        existingMarker.status = status;
                        
                        if (existingMarker.mesh) {
                            (existingMarker.mesh.material as THREE.MeshBasicMaterial).color.setHex(color);
                            existingMarker.mesh.position.copy(position);
                            existingMarker.mesh.lookAt(0, 0, 0);
                        }

                        if (existingMarker.label && (existingMarker.name !== name || !existingMarker.label.position.equals(position))) {
                            scene.remove(existingMarker.label);
                            existingMarker.label.material.map?.dispose();
                            existingMarker.label.material.dispose();
                            existingMarker.label = createLabelSprite(name, position) || undefined; // Ensure undefined if null
                            if (existingMarker.label) scene.add(existingMarker.label);
                        } else if (existingMarker.label) {
                             const labelOffset = 5;
                             existingMarker.label.position.set(position.x, position.y + labelOffset, position.z);
                        }
                        existingMarker.name = name;

                    } else {
                        const newMarker: FeedMarker = { id: feed.id, name, position, status };
                        const geometry = new THREE.ConeGeometry(0.8, 3, 8);
                        geometry.translate(0, 1.5, 0);
                        geometry.rotateX(Math.PI / 2);
                        const material = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.8 });
                        const mesh = new THREE.Mesh(geometry, material);
                        mesh.position.copy(position);
                        mesh.lookAt(new THREE.Vector3(0,0,0));
                        mesh.userData = { id: newMarker.id, name: newMarker.name, isMarker: true };
                        newMarker.mesh = mesh;
                        scene.add(mesh);

                        newMarker.label = createLabelSprite(name, position) || undefined; // Ensure undefined if null
                        if (newMarker.label) scene.add(newMarker.label);
                        
                        updatedMarkers[feed.id] = newMarker;
                    }
                });

                existingFeedIds.forEach(feedId => {
                    if (!incomingFeedIds.has(feedId)) {
                        const markerToRemove = updatedMarkers[feedId];
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
                        delete updatedMarkers[feedId];
                    }
                });
                return updatedMarkers;
            });
        };

        fetchAndUpdateFeeds();
        const intervalId = setInterval(fetchAndUpdateFeeds, 10000);

        return () => clearInterval(intervalId);
    }, [latLonAltToVector3]);


    const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const currentSceneRefs = sceneRef.current;
        if (!currentSceneRefs) return;
        const { camera, controls } = currentSceneRefs;
        const currentMarkers = feedMarkersRef.current;

        const searchTermValue = e.target.value.toLowerCase().trim();
        if (!searchTermValue) {
            controls.target.set(0,0,0);
            camera.position.set(0,0,120);
            controls.update();
            return;
        }

        const matchedFeed = Object.values(currentMarkers).find(
            (f: FeedMarker) =>
                f.name.toLowerCase().includes(searchTermValue) ||
                f.id.toLowerCase().includes(searchTermValue)
        );

        if (matchedFeed?.position) {
            const offsetDistance = 20;
            const directionToMarker = matchedFeed.position.clone().normalize();
            const desiredCameraPosition = matchedFeed.position.clone().add(directionToMarker.multiplyScalar(offsetDistance));
            
            const minFocusDistance = 50 + 15;
            if (desiredCameraPosition.length() < minFocusDistance) {
                desiredCameraPosition.normalize().multiplyScalar(minFocusDistance);
            }

            controls.target.copy(matchedFeed.position);
            camera.position.copy(desiredCameraPosition);
            controls.update();
        }
    };

    return (
        <div className="relative w-full h-[600px] overflow-hidden bg-black cursor-grab active:cursor-grabbing">
            <div ref={containerRef} className="absolute inset-0 w-full h-full" />
            <div className="absolute top-4 right-4 z-10 bg-black/60 p-3 rounded-md border border-green-700/50 shadow-lg">
                <input
                    type="text"
                    placeholder="Search feed name/ID..."
                    className="bg-transparent text-green-400 border-none focus:outline-none px-2 py-1 w-60 placeholder-green-600/70 text-sm"
                    onChange={handleSearchChange}
                />
            </div>
        </div>
    );
};

export default ThreeGrid;