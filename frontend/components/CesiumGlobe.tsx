"use client";
import React, { useRef, useEffect, useState, useCallback } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { gsap } from 'gsap';
import { useRouter } from 'next/navigation';
import type { FeedStatusData } from '@/lib/types';
import { db } from '@/lib/firebase'; // Assuming db is a Firestore instance from Firebase v9+
import { collection, onSnapshot } from 'firebase/firestore'; // Firebase v9+ imports

// Define Constants
const GLOBE_RADIUS = 50;
const MARKER_ALTITUDE = 0.5;
const ALERT_ALTITUDE = 2;
const CAMERA_INITIAL_DISTANCE = 120;
const CAMERA_MIN_DISTANCE = 60;
const CAMERA_MAX_DISTANCE = 300;
const CAMERA_OFFSET_DISTANCE = 20;
const CAMERA_ANIMATION_DURATION = 1.5;

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
    feed_id: string;
    name: string;
    position: THREE.Vector3;
    mesh?: THREE.Mesh<THREE.ConeGeometry, THREE.MeshBasicMaterial>;
 status: 'error' | 'stopped' | 'running' | 'starting' | 'stopping'; // Added 'stopping'
    latest_metrics?: Record<string, unknown>; // Add latest_metrics to the interface
}

interface AlertMarker {
    id: string | number;
    position: THREE.Vector3;
    mesh?: THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial>;
    severity: 'Critical' | 'Warning' | 'Anomaly' | 'INFO' | 'ERROR';
}

interface AlertData {
    id: string;
    latitude: number;
    longitude: number;
    severity: 'Critical' | 'Warning' | 'Anomaly' | 'INFO' | 'ERROR';
    message: string;
}

// Structure to hold the scene, camera, renderer and controls
interface SceneRefs {
    scene: THREE.Scene;
    camera: THREE.PerspectiveCamera;
    renderer: THREE.WebGLRenderer;
    controls: OrbitControls;
    animationId: number | null;
}




const ThreeGrid: React.FC = () => {
    const containerRef = useRef<HTMLDivElement>(null);
    const [feedMarkers, setFeedMarkers] = useState<Record<string, FeedMarker>>({});
    const [alertMarkers, setAlertMarkers] = useState<Record<string, AlertMarker>>({}); // New state for alert markers
    const [feedLabels, setFeedLabels] = useState<Record<string, { text: string; position: THREE.Vector3; screenPosition: { x: number; y: number; }; }>>({});
    const [alertLabels, setAlertLabels] = useState<Record<string, { text: string; position: THREE.Vector3; screenPosition: { x: number; y: number; }; }>>({});
    const router = useRouter();
    const sceneRef = useRef<SceneRefs | null>(null);
    const globeRef = useRef<THREE.Mesh | null>(null);
    const feedMarkersRef = useRef(feedMarkers);
    const alertMarkersRef = useRef(alertMarkers); // Ref for alert markers

    // Helper to convert 3D world position to 2D screen position
    const toScreenPosition = useCallback((position: THREE.Vector3, camera: THREE.PerspectiveCamera, renderer: THREE.WebGLRenderer) => {
        const vector = position.clone();
        vector.project(camera);

        vector.x = (vector.x * 0.5 + 0.5) * renderer.domElement.clientWidth;
        vector.y = (vector.y * -0.5 + 0.5) * renderer.domElement.clientHeight;

        return { x: vector.x, y: vector.y };
    }, []);

    useEffect(() => {
        feedMarkersRef.current = feedMarkers;
        // Update feed labels state
        const newFeedLabels: Record<string, { text: string; position: THREE.Vector3; screenPosition: { x: number; y: number; }; }> = {};
        Object.values(feedMarkers).forEach(marker => {
            let labelText = marker.name;
            // Add checks for latest_metrics and its properties
            if (marker.latest_metrics) {
                const metrics = marker.latest_metrics as { avg_speed?: number | null; vehicle_count?: number | null; }; // Cast for safer access
                if (metrics.avg_speed !== undefined && metrics.avg_speed !== null) {
                    labelText += `\nSpeed: ${metrics.avg_speed.toFixed(1)} km/h`;
                }
                if (metrics.vehicle_count !== undefined && metrics.vehicle_count !== null) {
                    labelText += `\nVehicles: ${metrics.vehicle_count}`;
                } // Added missing brace here
            }
            newFeedLabels[marker.feed_id] = { text: labelText, position: marker.position, screenPosition: { x: 0, y: 0 } };
        });
        setFeedLabels(newFeedLabels);
    }, [feedMarkers]);

    useEffect(() => {
        alertMarkersRef.current = alertMarkers;
        // Update alert labels state
        const newAlertLabels: Record<string, { text: string; position: THREE.Vector3; screenPosition: { x: number; y: number; }; }> = {};
        Object.values(alertMarkers).forEach(marker => {
            newAlertLabels[marker.id] = { text: marker.severity, position: marker.position, screenPosition: { x: 0, y: 0 } };
        });
        setAlertLabels(newAlertLabels);
    }, [alertMarkers]);

    // Helper to create feed meshes
    const createFeedMesh = useCallback((position: THREE.Vector3, status: FeedMarker['status']): THREE.Mesh<THREE.ConeGeometry, THREE.MeshBasicMaterial> => {
        const geometry = new THREE.ConeGeometry(0.5, 2, 8); // Cone shape
        const material = new THREE.MeshBasicMaterial({ color: (() => {
            switch (status) {
                case 'running': return 0x00FF00;
                case 'starting': return 0xFFA500;
                case 'stopped': return 0x808080;
                case 'error': return 0xFF0000;
                default: return 0x00FF00;
            }
        })(), transparent: true, opacity: 0.9 });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.position.copy(position);
        mesh.userData = { isFeed: true, status: status };
        return mesh;
    }, []);

    // Helper to create alert meshes
    const createAlertMesh = useCallback((position: THREE.Vector3, severity: AlertMarker['severity']): THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial> => {
        const geometry = new THREE.SphereGeometry(0.7, 16, 16); // Small sphere
        const material = new THREE.MeshBasicMaterial({ color: (() => {
            switch (severity) {
                case 'Critical': return 0xFF0000;
                case 'Warning': return 0xFFA500;
                case 'INFO': return 0x00FFFF;
                case 'ERROR': return 0x8B0000;
                case 'Anomaly': return 0xFF00FF;
                default: return 0x00FF00;
            }
        })(), transparent: true, opacity: 0.9 });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.position.copy(position);
        mesh.userData = { isAlert: true, severity: severity };
        return mesh;
    }, []);

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
        const latRad = lat * Math.PI / 180;
        const lonRad = lon * Math.PI / 180;

        const x = radius * Math.cos(latRad) * Math.cos(lonRad);
        const y = radius * Math.sin(latRad);
        const z = radius * Math.cos(latRad) * Math.sin(lonRad);
        return new THREE.Vector3(x, y, z);
    }, []);

    const addPolygonToScene = useCallback((polygonCoords: number[][][], scene: THREE.Scene) => {
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
                const material = new THREE.LineBasicMaterial({ color: 0x00ff00, opacity: 0.8, transparent: true, linewidth: 2 });
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
        const currentContainer = containerRef.current;
        if (!currentContainer || sceneRef.current) return;

        console.log("Initializing Three.js Scene");
        const scene = new THREE.Scene();
        scene.fog = new THREE.Fog(0x000000, 80, 250);

        // Add Starfield Background
        const starsGeometry = new THREE.BufferGeometry();
        const starsMaterial = new THREE.PointsMaterial({ color: 0x888888, size: 0.5, sizeAttenuation: true });
        const starVertices = [];
        for (let i = 0; i < 10000; i++) {
            const x = (Math.random() - 0.5) * 2000;
            const y = (Math.random() - 0.5) * 2000;
            const z = (Math.random() - 0.5) * 2000;
            starVertices.push(x, y, z);
        }
        starsGeometry.setAttribute('position', new THREE.Float32BufferAttribute(starVertices, 3));
        const stars = new THREE.Points(starsGeometry, starsMaterial);
        scene.add(stars);

        const camera = new THREE.PerspectiveCamera(75, currentContainer.clientWidth / currentContainer.clientHeight, 0.1, 1000);
        const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setClearColor(0x000000, 0);
        renderer.setSize(currentContainer.clientWidth, currentContainer.clientHeight);
        renderer.setPixelRatio(window.devicePixelRatio);
        currentContainer.appendChild(renderer.domElement);

        const globeGeometry = new THREE.SphereGeometry(GLOBE_RADIUS, 16, 16);
        const globeMaterial = new THREE.MeshBasicMaterial({
            color: 0x003300, wireframe: true, opacity: 0.5, transparent: true,
        });
        const globe = new THREE.Mesh(globeGeometry, globeMaterial);
        globeRef.current = globe;
        scene.add(globe);

        loadGeoJSON(scene);

        const controls = new OrbitControls(camera, renderer.domElement);
        controls.enableDamping = true;
        controls.dampingFactor = 0.05;
        controls.minDistance = CAMERA_MIN_DISTANCE;
        controls.maxDistance = CAMERA_MAX_DISTANCE;
        controls.enablePan = false;

        camera.position.set(0, 0, CAMERA_INITIAL_DISTANCE);
        camera.lookAt(scene.position);
        controls.update();

        sceneRef.current = { scene, camera, renderer, controls, animationId: null };

        const animateScene = () => {
            if (!sceneRef.current) return;
            const { scene, camera, renderer, controls } = sceneRef.current;
            sceneRef.current.animationId = requestAnimationFrame(animateScene);
            controls.update();
            renderer.render(scene, camera);

            // Update screen positions for HTML labels
            setFeedLabels(prev => {
                const updated = { ...prev };
                Object.keys(updated).forEach(id => {
                    const label = updated[id];
                    label.screenPosition = toScreenPosition(label.position, camera, renderer);
                });
                return updated;
            });
            setAlertLabels(prev => {
                const updated = { ...prev };
                Object.keys(updated).forEach(id => {
                    const label = updated[id];
                    label.screenPosition = toScreenPosition(label.position, camera, renderer);
                });
                return updated;
            });
        };
        animateScene(); // Start the animation loop

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

        return () => {
            console.log("Cleaning up Three.js Scene");
            const sceneRefsToClean = sceneRef.current;
            if (!sceneRefsToClean) return;

            window.removeEventListener('resize', handleResize);
            if (sceneRefsToClean.animationId) cancelAnimationFrame(sceneRefsToClean.animationId);
            
            sceneRefsToClean.controls?.dispose();
            // More robust cleanup of scene objects and materials
            // More robust cleanup of scene objects and materials
            sceneRefsToClean.scene?.traverse((object) => {
              if (object instanceof THREE.Mesh || object instanceof THREE.Line || object instanceof THREE.Points) { // Include Points for cleanup
                object.geometry?.dispose(); // Dispose of geometry
                // Check if material exists and is a valid Material instance
                }
            });
            while(sceneRefsToClean.scene?.children.length > 0){
                sceneRefsToClean.scene.remove(sceneRefsToClean.scene.children[0]);
            }
            if (currentContainer && sceneRefsToClean.renderer?.domElement) {
                 if (currentContainer.contains(sceneRefsToClean.renderer.domElement)) {
                    currentContainer.removeChild(sceneRefsToClean.renderer.domElement);
                 }
            }
            sceneRefsToClean.renderer?.dispose();
            sceneRef.current = null;
            globeRef.current = null;
            setFeedMarkers({});
        };
    }, [loadGeoJSON, toScreenPosition]);

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
                    if (intersectedObject.userData?.feed_id) {
                        router.push(`/surveillance/${intersectedObject.userData.feed_id}`);
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
        const latRad = lat * Math.PI / 180;
        const lonRad = lon * Math.PI / 180;
        const r = radius + alt;

        const x = r * Math.cos(latRad) * Math.cos(lonRad);
        const y = r * Math.sin(latRad);
        const z = r * Math.cos(latRad) * Math.sin(lonRad);
        return new THREE.Vector3(x, y, z);
    }, []);

    useEffect(() => {
        const currentSceneRefs = sceneRef.current;
        if (!currentSceneRefs) return;
        const { scene } = currentSceneRefs;

        const feedsCollection = collection(db!, 'feeds'); // Assert db is non-null
        const unsubscribe = onSnapshot(feedsCollection, (querySnapshot) => {
            const fetchedFeeds: FeedStatusData[] = [];
            querySnapshot.forEach((doc) => {
                fetchedFeeds.push({ feed_id: doc.id, ...doc.data() } as FeedStatusData);
            });

            setFeedMarkers(prevFeedMarkers => {
                const updatedFeedMarkers: Record<string, FeedMarker> = { ...prevFeedMarkers };
                const incomingFeedIds = new Set(fetchedFeeds.map(f => f.feed_id));
                const existingFeedIds = new Set(Object.keys(prevFeedMarkers));

                fetchedFeeds.forEach(feed => {
                    if (feed.latitude === undefined || feed.longitude === undefined) {
                        console.warn("Skipping feed due to missing coordinates:", feed);
                        return;
                    }
                    const existingFeedMarker = updatedFeedMarkers[feed.feed_id];
                    const position = latLonAltToVector3(feed.latitude, feed.longitude, MARKER_ALTITUDE, GLOBE_RADIUS);

                    if (existingFeedMarker) {
                        // Update existing feed marker
                        existingFeedMarker.position.copy(position);
                        existingFeedMarker.status = feed.status;
                        existingFeedMarker.latest_metrics = feed.latest_metrics as FeedMarker['latest_metrics'];
                        if (existingFeedMarker.mesh) {
                            switch (feed.status) {
                                case 'running': (existingFeedMarker.mesh.material as THREE.MeshBasicMaterial).color.setHex(0x00FF00); break;
                                case 'starting': (existingFeedMarker.mesh.material as THREE.MeshBasicMaterial).color.setHex(0xFFA500); break;
                                case 'stopped': (existingFeedMarker.mesh.material as THREE.MeshBasicMaterial).color.setHex(0x808080); break;
                                case 'error': (existingFeedMarker.mesh.material as THREE.MeshBasicMaterial).color.setHex(0xFF0000); break;
                                default: (existingFeedMarker.mesh.material as THREE.MeshBasicMaterial).color.setHex(0x00FF00);
                            }
                            existingFeedMarker.mesh.position.copy(position);
                        }
                    } else {
                        // Create new feed marker
                        const newFeedMarker: FeedMarker = {
                            feed_id: feed.feed_id,
                            name: feed.name ?? 'Unknown Feed', // Provide a default if name is undefined
                            position: position,
                            status: feed.status,
                            latest_metrics: feed.latest_metrics as FeedMarker['latest_metrics'],
                        };
                        const mesh = createFeedMesh(position, feed.status);
                        mesh.userData.id = feed.feed_id; // Store ID for raycasting
                        newFeedMarker.mesh = mesh;
                        scene.add(mesh);

                        updatedFeedMarkers[feed.feed_id] = newFeedMarker;
                    }
                });

                // Remove feeds that are no longer present
                existingFeedIds.forEach(feedId => {
                    if (!incomingFeedIds.has(feedId)) {
                        const markerToRemove = updatedFeedMarkers[feedId];
                        if (markerToRemove.mesh) {
                            scene.remove(markerToRemove.mesh);
                            markerToRemove.mesh.geometry.dispose();
                            (markerToRemove.mesh.material as THREE.Material).dispose();
                        }
                        delete updatedFeedMarkers[feedId];
                    }
                });
                return updatedFeedMarkers;
            });
        });

        return () => unsubscribe();
    }, [latLonAltToVector3, createFeedMesh]);

    useEffect(() => {
        const currentSceneRefs = sceneRef.current;
        if (!currentSceneRefs) return;
        const { scene } = currentSceneRefs;

        const alertsCollection = collection(db!, 'alerts'); // Assert db is non-null
        const unsubscribe = onSnapshot(alertsCollection, (querySnapshot) => {
            const fetchedAlerts: AlertData[] = [];
            querySnapshot.forEach((doc) => {
                fetchedAlerts.push({ id: doc.id, ...doc.data() } as AlertData);
            });

            setAlertMarkers(prevAlertMarkers => {
                const updatedAlertMarkers: Record<string, AlertMarker> = { ...prevAlertMarkers };
                const incomingAlertIds = new Set(fetchedAlerts.map(a => a.id?.toString()));
                const existingAlertIds = new Set(Object.keys(prevAlertMarkers));

                fetchedAlerts.forEach(alert => {
                    if (alert.id === undefined || alert.latitude === undefined || alert.longitude === undefined) {
                        console.warn("Skipping alert due to missing ID or coordinates:", alert);
                        return;
                    }
                    const alertId = alert.id.toString();
                    const existingAlertMarker = updatedAlertMarkers[alertId];

                    const position = latLonAltToVector3(alert.latitude, alert.longitude, ALERT_ALTITUDE, GLOBE_RADIUS);

                    if (existingAlertMarker) {
                        // Update existing alert marker
                        existingAlertMarker.position.copy(position);
                        existingAlertMarker.severity = alert.severity;
                        if (existingAlertMarker.mesh) {
                            switch (alert.severity) {
                                case 'Critical': (existingAlertMarker.mesh.material as THREE.MeshBasicMaterial).color.setHex(0xFF0000); break;
                                case 'Warning': (existingAlertMarker.mesh.material as THREE.MeshBasicMaterial).color.setHex(0xFFA500); break;
                                case 'INFO': (existingAlertMarker.mesh.material as THREE.MeshBasicMaterial).color.setHex(0x00FFFF); break;
                                case 'ERROR': (existingAlertMarker.mesh.material as THREE.MeshBasicMaterial).color.setHex(0x8B0000); break;
                                case 'Anomaly': (existingAlertMarker.mesh.material as THREE.MeshBasicMaterial).color.setHex(0xFF00FF); break;
                                default: (existingAlertMarker.mesh.material as THREE.MeshBasicMaterial).color.setHex(0x00FF00);
                            }
                            existingAlertMarker.mesh.position.copy(position);
                        }
                    } else {
                        // Create new alert marker
                        const newAlertMarker: AlertMarker = {
                            id: alertId,
                            position: position,
                            severity: alert.severity,
                        };
                        const mesh = createAlertMesh(position, alert.severity);
                        mesh.userData.id = alertId; // Store ID for raycasting
                        newAlertMarker.mesh = mesh;
                        scene.add(mesh);

                        updatedAlertMarkers[alertId] = newAlertMarker;
                    }
                });

                // Remove alerts that are no longer present
                existingAlertIds.forEach(alertId => {
                    if (!incomingAlertIds.has(alertId)) {
                        const markerToRemove = updatedAlertMarkers[alertId];
                        if (markerToRemove.mesh) {
                            scene.remove(markerToRemove.mesh);
                            markerToRemove.mesh.geometry.dispose();
                            (markerToRemove.mesh.material as THREE.Material).dispose();
                        }
                        delete updatedAlertMarkers[alertId];
                    }
                });
                return updatedAlertMarkers;
            });
        });

        return () => unsubscribe();
    }, [latLonAltToVector3, createAlertMesh]);


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
                f.feed_id.toLowerCase().includes(searchTermValue)
        );

        if (matchedFeed?.position) {
            const offsetDistance = CAMERA_OFFSET_DISTANCE;
            const directionToMarker = matchedFeed.position.clone().normalize();
            const desiredCameraPosition = matchedFeed.position.clone().add(directionToMarker.multiplyScalar(offsetDistance));
            
            const minFocusDistance = GLOBE_RADIUS + 15;
            if (desiredCameraPosition.length() < minFocusDistance) {
                desiredCameraPosition.normalize().multiplyScalar(minFocusDistance);
            }

            gsap.to(camera.position, {
                duration: CAMERA_ANIMATION_DURATION,
                x: desiredCameraPosition.x,
                y: desiredCameraPosition.y,
                z: desiredCameraPosition.z,
                ease: 'power3.inOut',
            });

            gsap.to(controls.target, {
                duration: CAMERA_ANIMATION_DURATION,
                x: matchedFeed.position.x,
                y: matchedFeed.position.y,
                z: matchedFeed.position.z,
                ease: 'power3.inOut',
                onUpdate: () => {
                    controls.update();
                },
            });
        }
    };

    return (
        <div className="relative w-full h-[600px] overflow-hidden cursor-grab active:cursor-grabbing">
            <div ref={containerRef} className="absolute inset-0 w-full h-full" />
            {Object.values(feedLabels).map(label => (
                <div
                    key={label.text} // Using text as key, assuming unique for simplicity
                    className="absolute text-xs font-bold text-black bg-green-400/70 p-1 rounded-sm pointer-events-none whitespace-pre"
                    style={{
                        left: `${label.screenPosition.x}px`,
                        top: `${label.screenPosition.y}px`,
                        transform: 'translate(-50%, -100%)',
                        display: label.screenPosition.x === 0 && label.screenPosition.y === 0 ? 'none' : 'block', // Hide if not calculated yet
                    }}
                >
                    {label.text}
                </div>
            ))}
            {Object.values(alertLabels).map(label => (
                <div
                    key={label.text} // Using text as key, assuming unique for simplicity
                    className="absolute text-xs font-bold text-black bg-red-400/70 p-1 rounded-sm pointer-events-none whitespace-pre"
                    style={{
                        left: `${label.screenPosition.x}px`,
                        top: `${label.screenPosition.y}px`,
                        transform: 'translate(-50%, -100%)',
                        display: label.screenPosition.x === 0 && label.screenPosition.y === 0 ? 'none' : 'block', // Hide if not calculated yet
                    }}
                >
                    {label.text}
                </div>
            ))}
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