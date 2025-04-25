"use client";
import React, { useRef, useEffect, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'; // Fixed import
import { useRouter } from 'next/navigation';
import type { FeedStatusData } from '@/lib/types';

interface FeedMarker {
    id: string;
    name: string;
    position: THREE.Vector3;
    mesh?: THREE.Mesh;
    label?: THREE.Sprite;
    status: 'error' | 'stopped' | 'running' | 'starting';
}

interface SceneRefs {
    scene: THREE.Scene;
    camera: THREE.PerspectiveCamera;
    renderer: THREE.WebGLRenderer;
    controls: OrbitControls;
    gridHelper: THREE.GridHelper | null;
}

const ThreeGrid: React.FC = () => {
    const containerRef = useRef<HTMLDivElement>(null);
    const [feeds, setFeeds] = useState<FeedMarker[]>([]);
    const router = useRouter();
    
    // Scene objects with proper typing
    const sceneRef = useRef<SceneRefs | null>(null);

    // Scene setup effect
    useEffect(() => {
        const container = containerRef.current;
        if (!container) return;

        // Setup
        const scene = new THREE.Scene();
        scene.fog = new THREE.Fog(0x000000, 50, 150);
        
        const camera = new THREE.PerspectiveCamera(75, container.clientWidth / container.clientHeight, 0.1, 1000);
        const renderer = new THREE.WebGLRenderer({ antialias: true });
        renderer.setClearColor(0x000000);
        renderer.setSize(container.clientWidth, container.clientHeight);
        container.appendChild(renderer.domElement);

        // Create a globe
        const globeGeometry = new THREE.SphereGeometry(50, 64, 64);
        const globeMaterial = new THREE.MeshBasicMaterial({
            color: 0x004400,
            wireframe: true,
            opacity: 0.8,
            transparent: true
        });
        const globe = new THREE.Mesh(globeGeometry, globeMaterial);
        scene.add(globe);

        // --- Add continent/country outlines from GeoJSON ---
        fetch('/continents.geojson')
            .then(res => res.json())
            .then(geojson => {
                const features = geojson.features || [];
                features.forEach((feature: any) => {
                    const coords = feature.geometry.coordinates;
                    const type = feature.geometry.type;
                    // Handle MultiPolygon and Polygon
                    const polygons = type === 'Polygon' ? [coords] : coords;
                    polygons.forEach((polygon: any) => {
                        polygon.forEach((ring: any) => {
                            const points: THREE.Vector3[] = ring.map(([lon, lat]: [number, number]) => {
                                // Convert lat/lon to 3D sphere coordinates
                                const phi = (90 - lat) * (Math.PI / 180);
                                const theta = (lon + 180) * (Math.PI / 180);
                                const radius = 50.1; // slightly above globe
                                return new THREE.Vector3(
                                    radius * Math.sin(phi) * Math.cos(theta),
                                    radius * Math.cos(phi),
                                    radius * Math.sin(phi) * Math.sin(theta)
                                );
                            });
                            const geometry = new THREE.BufferGeometry().setFromPoints(points);
                            const material = new THREE.LineBasicMaterial({ color: 0x00ff00, opacity: 0.7, transparent: true });
                            const line = new THREE.Line(geometry, material);
                            scene.add(line);
                        });
                    });
                });
            })
            .catch(() => {/* ignore if file not found */});
        // --- End continent/country outlines ---

        // Add orbit controls
        const controls = new OrbitControls(camera, renderer.domElement);
        controls.enableDamping = true;

        // Position camera
        camera.position.set(0, 100, 150);
        camera.lookAt(0, 0, 0);

        // Store references
        sceneRef.current = {
            scene,
            camera,
            renderer,
            controls,
            gridHelper: null // No grid in globe representation
        };

        // Handle resize
        const handleResize = () => {
            if (!container) return;
            const width = container.clientWidth;
            const height = container.clientHeight;
            camera.aspect = width / height;
            camera.updateProjectionMatrix();
            renderer.setSize(width, height);
        };
        window.addEventListener('resize', handleResize);

        // Animation
        let animationFrameId: number;
        const animate = () => {
            animationFrameId = requestAnimationFrame(animate);
            controls.update();
            renderer.render(scene, camera);
        };
        animate();

        // Cleanup
        return () => {
            window.removeEventListener('resize', handleResize);
            if (container) {
                container.removeChild(renderer.domElement);
            }
            cancelAnimationFrame(animationFrameId);
            scene.remove(globe);
            renderer.dispose();
        };
    }, []);

    // Click interaction effect
    useEffect(() => {
        const container = containerRef.current;
        if (!container) return;

        const raycaster = new THREE.Raycaster();
        const mouse = new THREE.Vector2();

        const handleClick = (event: MouseEvent) => {
            if (!container || !sceneRef.current) return;

            const rect = container.getBoundingClientRect();
            mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
            mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

            raycaster.setFromCamera(mouse, sceneRef.current.camera);
            const intersects = raycaster.intersectObjects(sceneRef.current.scene.children);

            if (intersects.length > 0) {
                const mesh = intersects[0].object;
                if (mesh.userData?.id) {
                    router.push(`/surveillance/${mesh.userData.id}`);
                }
            }
        };

        container.addEventListener('click', handleClick);
        return () => {
            if (container) {
                container.removeEventListener('click', handleClick);
            }
        };
    }, [router]);

    // Feed update effect
    useEffect(() => {
        const fetchAndUpdateFeeds = async () => {
            if (!sceneRef.current) return;

            try {
                const response = await fetch('/api/feeds');
                const data: FeedStatusData[] = await response.json();

                const gridSize = Math.ceil(Math.sqrt(data.length));
                const spacing = 20;

                // Update markers
                const newMarkers = data.map((feed, index) => {
                    const row = Math.floor(index / gridSize);
                    const col = index % gridSize;
                    const x = (col - gridSize / 2) * spacing;
                    const z = (row - gridSize / 2) * spacing;

                    const marker: FeedMarker = {
                        id: feed.id,
                        name: feed.name || feed.source,
                        position: new THREE.Vector3(x, 0, z),
                        status: feed.status as FeedMarker['status']
                    };

                    // Create marker mesh
                    const geometry = new THREE.CylinderGeometry(1, 0, 4, 4);
                    const material = new THREE.MeshBasicMaterial({
                        color: feed.status === 'running' ? 0x00ff00 : 0x666666,
                        transparent: true,
                        opacity: 0.8
                    });
                    const mesh = new THREE.Mesh(geometry, material);
                    mesh.position.copy(marker.position);
                    mesh.userData = { id: marker.id, name: marker.name };
                    marker.mesh = mesh;

                    // Create label
                    const canvas = document.createElement('canvas');
                    const context = canvas.getContext('2d');
                    if (context) {
                        canvas.width = 256;
                        canvas.height = 64;
                        context.fillStyle = '#000000';
                        context.fillRect(0, 0, canvas.width, canvas.height);
                        context.font = '24px monospace';
                        context.textAlign = 'center';
                        context.fillStyle = '#00ff00';
                        context.fillText(marker.name, canvas.width / 2, canvas.height / 2);
                    }

                    const texture = new THREE.CanvasTexture(canvas);
                    const spriteMaterial = new THREE.SpriteMaterial({
                        map: texture,
                        transparent: true,
                        opacity: 0.8
                    });
                    const label = new THREE.Sprite(spriteMaterial);
                    label.position.set(marker.position.x, marker.position.y + 5, marker.position.z);
                    label.scale.set(10, 2.5, 1);
                    marker.label = label;

                    return marker;
                });

                setFeeds((prevFeeds) => {
                    // Remove old markers
                    prevFeeds.forEach(feed => {
                        if (feed.mesh) sceneRef.current?.scene.remove(feed.mesh);
                        if (feed.label) sceneRef.current?.scene.remove(feed.label);
                    });

                    // Add new markers
                    newMarkers.forEach(marker => {
                        if (marker.mesh) sceneRef.current?.scene.add(marker.mesh);
                        if (marker.label) sceneRef.current?.scene.add(marker.label);
                    });

                    return newMarkers;
                });
            } catch (error) {
                console.error('Error fetching feeds:', error);
            }
        };

        fetchAndUpdateFeeds();
        const interval = setInterval(fetchAndUpdateFeeds, 5000);

        return () => clearInterval(interval);
    }, []); // Dependency array remains empty as state updates are handled internally

    // Animation update effect
    useEffect(() => {
        if (!sceneRef.current) return;

        const animate = () => {
            feeds.forEach(feed => {
                if (feed.mesh && feed.label) {
                    feed.mesh.rotation.y += 0.02;
                    if (feed.status === 'running') {
                        const opacity = 0.5 + Math.sin(Date.now() * 0.003) * 0.3;
                        (feed.mesh.material as THREE.MeshBasicMaterial).opacity = opacity;
                        feed.label.material.opacity = 0.6 + Math.sin(Date.now() * 0.003) * 0.2;
                    }
                }
            });

            sceneRef.current?.renderer.render(sceneRef.current.scene, sceneRef.current.camera);
            requestAnimationFrame(animate);
        };

        const animationId = requestAnimationFrame(animate);
        return () => cancelAnimationFrame(animationId);
    }, [feeds]);

    return (
        <div className="relative w-full h-[600px]">
            <div ref={containerRef} className="w-full h-full" />
            <div className="absolute top-4 right-4 bg-black/80 p-4 rounded-lg border border-green-500">
                <input
                    type="text"
                    placeholder="Search location..."
                    className="bg-black text-green-500 border border-green-500 rounded px-3 py-2 w-64"
                    onChange={(e) => {
                        if (!sceneRef.current) return;
                        
                        const searchTerm = e.target.value.toLowerCase();
                        const matchedFeed = feeds.find(f => 
                            f.name.toLowerCase().includes(searchTerm) || 
                            f.id.toLowerCase().includes(searchTerm)
                        );

                        if (matchedFeed) {
                            const { camera, controls } = sceneRef.current;
                            const targetPosition = matchedFeed.position.clone();
                            targetPosition.y = camera.position.y;
                            camera.position.lerp(targetPosition.add(new THREE.Vector3(10, 10, 10)), 0.1);
                            controls.target.copy(matchedFeed.position);
                        }
                    }}
                />
            </div>
        </div>
    );
};

export default ThreeGrid;