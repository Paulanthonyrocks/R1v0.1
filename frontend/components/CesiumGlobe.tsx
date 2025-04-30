"use client";
import React, { useRef, useEffect, useState, useCallback } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { useRouter } from 'next/navigation';
import type { FeedStatusData } from '@/lib/types';

// Define GeoJSON Interfaces
interface GeoJSONFeature {
    type: 'Feature';
    // Define geometry types more explicitly for clarity
    geometry: { type: 'Polygon'; coordinates: number[][][] } | { type: 'MultiPolygon'; coordinates: number[][][][] };
    properties: Record<string, unknown>; // Can hold any properties
}

interface GeoJSON {
    type: 'FeatureCollection';
    features: GeoJSONFeature[];
}

// Define FeedMarker Interface
interface FeedMarker {
    // Represents a feed marker in the 3D scene
    id: string;
    name: string;
    position: THREE.Vector3;
    // More specific type for mesh material if known (e.g., MeshBasicMaterial)
    mesh?: THREE.Mesh<THREE.ConeGeometry, THREE.MeshBasicMaterial>;
    // More specific type for label material if known (e.g., SpriteMaterial)
    label?: THREE.Sprite;
    status: 'error' | 'stopped' | 'running' | 'starting';
}

// Structure to hold the scene, camera, renderer and controls
interface SceneRefs {
    scene: THREE.Scene;
    camera: THREE.PerspectiveCamera;
    renderer: THREE.WebGLRenderer;
    controls: OrbitControls;
    animationId: number | null; // Store the animation frame ID
}

const ThreeGrid: React.FC = () => {
    const containerRef = useRef<HTMLDivElement>(null);
    // Use a map for efficient lookup, update, and removal by ID
    const [feedMarkers, setFeedMarkers] = useState<Record<string, FeedMarker>>({});
    const router = useRouter();
    const sceneRef = useRef<SceneRefs | null>(null);
    // Keep track of the globe mesh for potential interactions
    const globeRef = useRef<THREE.Mesh | null>(null);
    // Ref to hold the latest feedMarkers state for use in animation loop without causing effect re-runs
    const feedMarkersRef = useRef(feedMarkers);

    // Update the ref whenever feedMarkers state changes
    useEffect(() => {
        feedMarkersRef.current = feedMarkers;
    }, [feedMarkers]);


    // --- GeoJSON Loading Logic ---

    // Helper to check if an object is a valid GeoJSONFeature
    const isValidGeoJSONFeature = (f: unknown): f is GeoJSONFeature => {
        if (typeof f !== 'object' || f === null) return false;
        const feature = f as GeoJSONFeature;
        return (
            feature.type === 'Feature' &&
            typeof feature.geometry === 'object' &&
            (feature.geometry.type === 'Polygon' || feature.geometry.type === 'MultiPolygon') &&
            Array.isArray(feature.geometry.coordinates)
        );
    };

    // Helper to convert lon/lat to 3D point on sphere
    const lonLatToVector3 = useCallback((lon: number, lat: number, radius: number = 50.1): THREE.Vector3 => {
        // Clamp latitude to avoid issues at poles if necessary
        // lat = Math.max(-89.99, Math.min(89.99, lat));
        const phi = (90 - lat) * (Math.PI / 180); // Latitude to Spherical Phi angle
        const theta = (lon + 180) * (Math.PI / 180); // Longitude to Spherical Theta angle
        // Three.js coordinate system: Y up
        return new THREE.Vector3(
            -radius * Math.sin(phi) * Math.cos(theta), // x = -r * sin(phi) * cos(theta)
            radius * Math.cos(phi),                   // y = r * cos(phi)
            radius * Math.sin(phi) * Math.sin(theta)    // z = r * sin(phi) * sin(theta)
        );
    }, []); // No dependencies, function is stable

    // Helper to process one polygon's coordinates and add lines to the scene
    const addPolygonToScene = useCallback((polygonCoords: number[][][], scene: THREE.Scene, color: THREE.ColorRepresentation = 0x00cc00) => { // Slightly brighter green
        polygonCoords.forEach((ringCoords: number[][]) => {
            // Basic validation for a ring
            if (!Array.isArray(ringCoords) || ringCoords.length < 3 || !Array.isArray(ringCoords[0]) || ringCoords[0].length !== 2) {
                // console.warn("Skipping invalid ring coordinates:", ringCoords);
                return; // Skip potentially invalid rings
            }

            const points = ringCoords.map((coord: number[]) => {
                // Validate coordinate pair
                if (Array.isArray(coord) && coord.length === 2 && typeof coord[0] === 'number' && typeof coord[1] === 'number') {
                    return lonLatToVector3(coord[0], coord[1]);
                }
                console.warn("Skipping invalid coordinate pair:", coord);
                return null; // Return null for invalid points
            }).filter((p): p is THREE.Vector3 => p !== null); // Filter out nulls

            if (points.length > 1) {
                // Close the loop if the first and last points aren't the same (GeoJSON standard)
                // Check distance as floating point equality can be tricky
                if (points[0].distanceToSquared(points[points.length - 1]) > 0.0001) {
                    points.push(points[0].clone());
                }
                const geometry = new THREE.BufferGeometry().setFromPoints(points);
                // Make lines slightly thinner and less opaque
                const material = new THREE.LineBasicMaterial({ color: color, opacity: 0.5, transparent: true, linewidth: 1 });
                const line = new THREE.Line(geometry, material);
                line.userData.isGeoJsonLine = true; // Mark for easy removal/identification later if needed
                scene.add(line);
            }
        });
    }, [lonLatToVector3]); // Dependency on lonLatToVector3

    // Load continent outlines
    const loadGeoJSON = useCallback(async (scene: THREE.Scene): Promise<void> => {
        console.log('Loading GeoJSON...');
        try {
            const response = await fetch('/continents.geojson'); // Ensure this path is correct
            if (!response.ok) {
                const errorText = await response.text().catch(() => 'Failed to read error text');
                console.error(`HTTP error! status: ${response.status}, message: ${errorText}`);
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const geojson: GeoJSON = await response.json().catch((error) => {
                console.error('Failed to parse GeoJSON:', error);
                throw new Error('Failed to parse GeoJSON');
            });

            if (geojson.type !== 'FeatureCollection' || !Array.isArray(geojson.features)) {
                 console.error('Invalid GeoJSON format: Expected FeatureCollection with features array.');
                 return;
            }

            const features: GeoJSONFeature[] = geojson.features;
            features.forEach((feature: unknown, index: number) => {
                if (!isValidGeoJSONFeature(feature)) {
                    // console.warn(`Skipping invalid GeoJSON feature at index ${index}:`, feature);
                    return;
                }

                const geometryType = feature.geometry.type;
                const coordinates = feature.geometry.coordinates;

                try {
                    if (geometryType === 'Polygon') {
                        addPolygonToScene(coordinates as number[][][], scene);
                    } else if (geometryType === 'MultiPolygon') {
                        const multiPolygonCoords = coordinates as number[][][][];
                        multiPolygonCoords.forEach((polygonCoords: number[][][]) => {
                            addPolygonToScene(polygonCoords, scene);
                        });
                    }
                } catch (processingError) {
                    console.error(`Error processing feature ${index} (${geometryType}):`, processingError, feature);
                }
            });
            console.log('GeoJSON loaded successfully.');

        } catch (error) {
            // Avoid cascading errors if fetch/parse fails
            console.error('An unexpected error occurred while loading GeoJSON:', error);
        }
    }, [addPolygonToScene]); // Dependency on addPolygonToScene


    // --- Scene Setup Effect ---
    useEffect(() => {
        const currentContainer = containerRef.current; // Capture ref value
        if (!currentContainer || sceneRef.current) return; // Exit if no container or already initialized

        console.log("Initializing Three.js Scene");

        // Create scene, camera, and renderer
        const scene = new THREE.Scene();
        scene.fog = new THREE.Fog(0x000000, 80, 250); // Adjusted fog

        const camera = new THREE.PerspectiveCamera(75, currentContainer.clientWidth / currentContainer.clientHeight, 0.1, 1000);
        const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setClearColor(0x000000, 0); // Transparent background
        renderer.setSize(currentContainer.clientWidth, currentContainer.clientHeight);
        renderer.setPixelRatio(window.devicePixelRatio);
        currentContainer.appendChild(renderer.domElement);

        // Create a globe
        const globeGeometry = new THREE.SphereGeometry(50, 64, 64); // Radius 50
        const globeMaterial = new THREE.MeshBasicMaterial({
            color: 0x001133, // Dark blue wireframe
            wireframe: true,
            opacity: 0.6,
            transparent: true,
        });
        const globe = new THREE.Mesh(globeGeometry, globeMaterial);
        globeRef.current = globe; // Store globe ref
        scene.add(globe);

        // Load outlines onto the globe
        loadGeoJSON(scene);

        // Add orbit controls
        const controls = new OrbitControls(camera, renderer.domElement);
        controls.enableDamping = true;
        controls.dampingFactor = 0.05;
        controls.minDistance = 60; // Prevent zooming inside too much
        controls.maxDistance = 300; // Limit zoom out
        controls.enablePan = false; // Disable panning

        // Position camera
        camera.position.set(0, 0, 120); // Start further out
        camera.lookAt(scene.position); // Look at the center (0,0,0)
        controls.update(); // Important after setting camera position

        // Store references
        sceneRef.current = {
            scene,
            camera,
            renderer,
            controls,
            animationId: null // Initialize animationId
        };

        // Handle window resize
        const handleResize = () => {
            // Use captured currentContainer, check sceneRef exists
            if (!currentContainer || !sceneRef.current) return;
            const { camera, renderer } = sceneRef.current;
            const width = currentContainer.clientWidth;
            const height = currentContainer.clientHeight;
            camera.aspect = width / height;
            camera.updateProjectionMatrix();
            renderer.setSize(width, height);
        };
        window.addEventListener('resize', handleResize);

        // --- Animation Loop ---
        const animateScene = () => {
            const currentSceneRefs = sceneRef.current; // Capture ref for this frame
            if (!currentSceneRefs) return; // Stop if scene is disposed

            // Use the marker ref to get the latest markers without causing effect re-runs
            const currentMarkers = feedMarkersRef.current;

            // Animate markers
            Object.values(currentMarkers).forEach(marker => {
                if (marker.mesh) {
                    marker.mesh.rotation.y += 0.01; // Simple rotation

                    // Pulse opacity for 'running' status
                    if (marker.status === 'running') {
                        const pulseFactor = 0.8 + Math.sin(Date.now() * 0.005) * 0.2;
                        // Safely assign opacity if material exists
                        if (marker.mesh.material) {
                            marker.mesh.material.opacity = pulseFactor;
                        }
                        if (marker.label?.material) { // Check label and its material
                            marker.label.material.opacity = pulseFactor;
                        }
                    } else {
                        // Reset opacity if not running
                        if (marker.mesh.material) {
                            marker.mesh.material.opacity = 0.8;
                        }
                         if (marker.label?.material) {
                            marker.label.material.opacity = 0.8;
                        }
                    }
                }
            });

            // Update controls and render
            currentSceneRefs.controls.update();
            currentSceneRefs.renderer.render(currentSceneRefs.scene, currentSceneRefs.camera);

            // Request next frame
            currentSceneRefs.animationId = requestAnimationFrame(animateScene);
        };
        // Start the animation loop
        animateScene();


        // --- Cleanup ---
        return () => {
            console.log("Cleaning up Three.js Scene");
            const sceneRefsToClean = sceneRef.current; // Get refs at cleanup time
            if (!sceneRefsToClean) return; // Safety check

            window.removeEventListener('resize', handleResize);

            // Stop animation loop
            if (sceneRefsToClean.animationId) {
                cancelAnimationFrame(sceneRefsToClean.animationId);
                sceneRefsToClean.animationId = null; // Clear the ID
            }

            // Dispose controls
            sceneRefsToClean.controls?.dispose(); // Optional chaining for safety

            // Dispose THREE.js objects
            if (sceneRefsToClean.scene) {
                sceneRefsToClean.scene.traverse((object) => {
                    if (object instanceof THREE.Mesh || object instanceof THREE.Line || object instanceof THREE.Sprite) {
                        object.geometry?.dispose(); // Dispose geometry if exists
                        // Handle material disposal carefully
                        if (object.material) {
                            if (Array.isArray(object.material)) {
                                object.material.forEach(mat => {
                                    mat.map?.dispose(); // Dispose texture if exists
                                    mat.dispose();
                                });
                            } else {
                                object.material.map?.dispose(); // Dispose texture if exists
                                object.material.dispose();
                            }
                        }
                    }
                });
                // Clear scene children after disposal
                while(sceneRefsToClean.scene.children.length > 0){
                    sceneRefsToClean.scene.remove(sceneRefsToClean.scene.children[0]);
                }
            }

            // Remove canvas from DOM
            if (currentContainer && sceneRefsToClean.renderer?.domElement) {
                 if (currentContainer.contains(sceneRefsToClean.renderer.domElement)) {
                    currentContainer.removeChild(sceneRefsToClean.renderer.domElement);
                 }
            }

             // Dispose renderer
            sceneRefsToClean.renderer?.dispose(); // Optional chaining

             // Clear refs
             sceneRef.current = null;
             globeRef.current = null;
             feedMarkersRef.current = {}; // Clear marker ref
             setFeedMarkers({}); // Clear state as well
        };
    }, [loadGeoJSON]); // useEffect depends on loadGeoJSON


    // --- Interaction: Handle clicks on the scene objects ---
    useEffect(() => {
        const currentContainer = containerRef.current;
        if (!currentContainer) return; // Ensure container exists

        const raycaster = new THREE.Raycaster();
        const mouse = new THREE.Vector2();

        const handleClick = (event: MouseEvent): void => {
            // Ensure sceneRef is still valid inside the handler
            const currentSceneRefs = sceneRef.current;
            if (!currentContainer || !currentSceneRefs) return;
            const { camera } = currentSceneRefs;
            const currentMarkers = feedMarkersRef.current; // Use the ref for latest markers

            // Calculate mouse position in normalized device coordinates (-1 to +1)
            const rect = currentContainer.getBoundingClientRect();
            mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
            mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

            raycaster.setFromCamera(mouse, camera);

            // Filter marker meshes for intersection test
            const markerMeshes = Object.values(currentMarkers)
                .map(f => f.mesh)
                .filter((m): m is THREE.Mesh<THREE.ConeGeometry, THREE.MeshBasicMaterial> => m instanceof THREE.Mesh);

            if (markerMeshes.length === 0) return; // No markers to intersect with

            const intersects = raycaster.intersectObjects(markerMeshes);

            if (intersects.length > 0) {
                // Clicked on a marker mesh
                const intersectedObject = intersects[0].object;
                if (intersectedObject.userData?.id) {
                    console.log('Clicked on feed:', intersectedObject.userData.name || intersectedObject.userData.id);
                    router.push(`/surveillance/${intersectedObject.userData.id}`);
                } else {
                     console.warn("Clicked on marker mesh without ID", intersectedObject);
                }
            } else {
                // Optional: Check if clicked on the globe
                const currentGlobe = globeRef.current;
                if (currentGlobe) {
                    const globeIntersects = raycaster.intersectObject(currentGlobe);
                    if (globeIntersects.length > 0) {
                        const point = globeIntersects[0].point;
                        console.log("Clicked on globe at point:", point);
                        // Could convert point back to approx lat/lon if needed
                    }
                }
            }
        };

        // Add click event listener
        currentContainer.addEventListener('click', handleClick);
        // Cleanup: Remove event listener
        return () => {
            if (currentContainer) {
                currentContainer.removeEventListener('click', handleClick);
            }
        };
    }, [router]); // Re-run only if router changes (or on mount/unmount)


    // Helper function to convert Lat/Lon/Alt to Cartesian for markers on the globe
    const latLonAltToVector3 = useCallback((lat: number, lon: number, alt: number = 0, radius: number = 50): THREE.Vector3 => {
        const phi = (90 - lat) * Math.PI / 180;
        const theta = (lon + 180) * Math.PI / 180;
        const r = radius + alt; // Add altitude to the base radius
        // Three.js coordinate system: Y up
        const x = -(r * Math.sin(phi) * Math.cos(theta));
        const y = r * Math.cos(phi);
        const z = r * Math.sin(phi) * Math.sin(theta);

        return new THREE.Vector3(x, y, z);
    }, []); // Stable function


    // --- Feed Update Effect ---
    useEffect(() => {
        const fetchAndUpdateFeeds = async () => {
            // Use ref to access scene safely within async function
            const currentSceneRefs = sceneRef.current;
            if (!currentSceneRefs) {
                // console.log("Scene not ready, skipping feed update.");
                return;
            }
            const { scene } = currentSceneRefs; // Destructure scene here now that we know ref is valid

            console.log('Fetching and updating feeds...');
            let data: FeedStatusData[] = [];
            try {
                const response = await fetch('/api/feeds'); // Ensure API endpoint is correct
                if (!response.ok) {
                    console.error(`Failed to fetch feeds: ${response.status} ${response.statusText}`);
                    return;
                }
                data = await response.json();
                if (!Array.isArray(data)) {
                    console.error("Fetched feed data is not an array:", data);
                    return;
                }
            } catch (error) {
                console.error('Error fetching or parsing feeds:', error);
                return;
            }

            // --- Update Markers State ---
            setFeedMarkers((prevFeedMarkers) => {
                const updatedMarkers: Record<string, FeedMarker> = {}; // Start fresh for easier logic
                const incomingFeedIds = new Set(data.map(f => f.id));

                // Process incoming feeds: Update existing or create new
                data.forEach((feed, index) => {
                    const existingMarker = prevFeedMarkers[feed.id];

                    // --- Calculate Position ---
                    const totalFeeds = data.length || 1;
                    const lon = (index / totalFeeds) * 360 * 3 - 180;
                    const lat = Math.acos(1 - 2 * (index / totalFeeds)) * (180 / Math.PI) - 90;
                    const altitude = 1;
                    const position = latLonAltToVector3(lat, lon, altitude, 50);

                    // --- Determine Status and Appearance ---
                    const status = (feed.status as FeedMarker['status']) || 'stopped';
                    const color = status === 'running' ? 0x00ff00 : (status === 'error' ? 0xff0000 : 0xcccccc);
                    const name = feed.name || feed.source || `Feed ${feed.id}`;

                    if (existingMarker) {
                        // --- Update Existing Marker ---
                        existingMarker.position.copy(position);
                        existingMarker.status = status;
                        existingMarker.name = name;

                        if (existingMarker.mesh?.material) {
                            existingMarker.mesh.material.color.setHex(color);
                        }
                        existingMarker.mesh?.position.copy(position);
                        existingMarker.mesh?.lookAt(0, 0, 0);

                        if (existingMarker.label && existingMarker.label.userData.name !== name) {
                            existingMarker.label.userData.name = name;
                        }
                        if (existingMarker.label) {
                            const labelOffset = 5;
                            existingMarker.label.position.set(position.x, position.y + labelOffset, position.z);
                        }
                        updatedMarkers[feed.id] = existingMarker;
                        if (existingMarker.mesh) existingMarker.mesh.visible = true; // Ensure visible
                        if (existingMarker.label) existingMarker.label.visible = true; // Ensure visible

                    } else {
                        // --- Create New Marker ---
                        const newMarker: FeedMarker = { id: feed.id, name: name, position: position, status: status };
                        const geometry = new THREE.ConeGeometry(0.8, 3, 8);
                        geometry.translate(0, 1.5, 0);
                        geometry.rotateX(Math.PI / 2);
                        const material = new THREE.MeshBasicMaterial({ color: color, transparent: true, opacity: 0.8 });
                        const mesh = new THREE.Mesh(geometry, material);
                        mesh.position.copy(newMarker.position);
                        mesh.lookAt(new THREE.Vector3(0,0,0));
                        mesh.userData = { id: newMarker.id, name: newMarker.name };
                        newMarker.mesh = mesh;
                        scene.add(mesh); // Add mesh using the scene obtained earlier

                        // Create label sprite
                        try {
                            const canvas = document.createElement('canvas');
                            const context = canvas.getContext('2d');
                            if (context) {
                                const fontSize = 18;
                                canvas.width = 256;
                                canvas.height = 64;
                                context.font = `Bold ${fontSize}px monospace`;
                                context.fillStyle = 'rgba(0, 0, 0, 0.6)';
                                context.fillRect(0, 0, canvas.width, canvas.height);
                                context.textAlign = 'center';
                                context.textBaseline = 'middle';
                                context.fillStyle = '#FFFFFF';
                                context.fillText(newMarker.name, canvas.width / 2, canvas.height / 2);
                                const texture = new THREE.CanvasTexture(canvas);
                                texture.needsUpdate = true;
                                const spriteMaterial = new THREE.SpriteMaterial({ map: texture, transparent: true, opacity: 0.8, sizeAttenuation: false });
                                const label = new THREE.Sprite(spriteMaterial);
                                const labelOffset = 5;
                                label.position.set(position.x, position.y + labelOffset, position.z);
                                label.scale.set(0.08 * canvas.width, 0.08 * canvas.height, 1.0);
                                label.userData = { name: newMarker.name };
                                newMarker.label = label;
                                scene.add(label); // Add label using the scene
                            }
                        } catch (labelError) {
                             console.error("Error creating label canvas/texture:", labelError);
                        }
                        updatedMarkers[feed.id] = newMarker;
                    }
                });

                // Remove markers for feeds that no longer exist in the incoming data
                Object.keys(prevFeedMarkers).forEach(feedId => {
                    if (!incomingFeedIds.has(feedId)) {
                        console.log("Removing marker for feed:", feedId);
                        const markerToRemove = prevFeedMarkers[feedId];
                        if (markerToRemove.mesh) scene.remove(markerToRemove.mesh); // Remove using scene
                        if (markerToRemove.label) scene.remove(markerToRemove.label); // Remove using scene
                    }
                });
                return updatedMarkers;
            });
        };

        fetchAndUpdateFeeds();
        const interval = setInterval(fetchAndUpdateFeeds, 10000);
        return () => clearInterval(interval);
    }, [latLonAltToVector3]); // Dependency remains the same


    // --- Search/Focus Logic ---
    const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const currentSceneRefs = sceneRef.current;
        if (!currentSceneRefs) return;
        const { camera, controls } = currentSceneRefs;
        const currentMarkers = feedMarkersRef.current;

        const searchTermValue = e.target.value.toLowerCase().trim();

        if (!searchTermValue) return;

        const matchedFeed = Object.values(currentMarkers).find((f: FeedMarker) =>
            f.name.toLowerCase().includes(searchTermValue) ||
            f.id.toLowerCase().includes(searchTermValue)
        );

        if (matchedFeed?.position) { // Check if position exists
            console.log("Focusing on feed:", matchedFeed.name);
             controls.target.copy(matchedFeed.position);
             const direction = new THREE.Vector3().subVectors(camera.position, controls.target).normalize();
             const distance = Math.max(camera.position.distanceTo(controls.target), 75); // Ensure min distance
             const desiredCameraPosition = new THREE.Vector3().addVectors(matchedFeed.position, direction.multiplyScalar(distance));

             // Avoid camera going inside the globe
             const minFocusDistance = 65;
             if (desiredCameraPosition.length() < minFocusDistance) {
                desiredCameraPosition.normalize().multiplyScalar(minFocusDistance);
             }

             camera.position.copy(desiredCameraPosition);
             controls.update();
             // TODO: Implement smooth camera transition
        }
    };

    // --- Render Component ---
    return (
        <div className="relative w-full h-[600px] overflow-hidden bg-black cursor-grab active:cursor-grabbing">
            {/* Container for the Three.js canvas */}
            <div ref={containerRef} className="absolute inset-0 w-full h-full" />

            {/* Search input overlay */}
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