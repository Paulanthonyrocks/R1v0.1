"use client";

import React, { useEffect, useRef, useState } from 'react';
import { Camera } from 'lucide-react';
import { useAuth } from '@/lib/auth/AuthProvider';
import { getBackendBaseURL } from '@/lib/api/backendBaseUrl';

const API_BASE_URL = getBackendBaseURL();

interface HeatmapProps {
    feed_id?: string;
    global_id?: string;
    hours?: number;
    width?: number;
    height?: number;
}

export const TrafficHeatmap: React.FC<HeatmapProps> = ({ feed_id, global_id, hours = 1, width = 640, height = 480 }) => {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const [loading, setLoading] = useState(true);
    const { token } = useAuth();

    useEffect(() => {
        const fetchData = async () => {
            if (!token) return;
            setLoading(true);
            try {
                let url = `${API_BASE_URL}/api/v1/analytics/heatmap?hours=${hours}`;
                if (feed_id) url += `&feed_id=${feed_id}`;
                if (global_id) url += `&global_id=${global_id}`;
                
                const res = await fetch(url, {
                    headers: {
                        'Bypass-Tunnel-Reminder': 'true',
                        'Authorization': `Bearer ${token}`
                    }
                });
                if (res.ok) {
                    const points = await res.json();
                    drawHeatmap(points);
                }
            } catch (e) {
                console.error("Heatmap fetch failed:", e);
            } finally {
                setLoading(false);
            }
        };

        const drawHeatmap = (points: {center_x: number, center_y: number}[]) => {
            const canvas = canvasRef.current;
            if (!canvas) return;
            const ctx = canvas.getContext('2d');
            if (!ctx) return;

            // Clear
            ctx.fillStyle = '#000';
            ctx.fillRect(0, 0, width, height);

            // Simple additive blending for heat effect
            ctx.globalCompositeOperation = 'lighter';
            
            points.forEach(p => {
                const grad = ctx.createRadialGradient(p.center_x, p.center_y, 0, p.center_x, p.center_y, 20);
                grad.addColorStop(0, 'rgba(0, 255, 0, 0.15)');
                grad.addColorStop(1, 'rgba(0, 255, 0, 0)');
                
                ctx.fillStyle = grad;
                ctx.beginPath();
                ctx.arc(p.center_x, p.center_y, 20, 0, Math.PI * 2);
                ctx.fill();
            });

            // Draw grid for retro feel
            ctx.globalCompositeOperation = 'source-over';
            ctx.strokeStyle = 'rgba(0, 255, 0, 0.05)';
            ctx.lineWidth = 1;
            for(let x=0; x<width; x+=40) {
                ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke();
            }
            for(let y=0; y<height; y+=40) {
                ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke();
            }
        };

        fetchData();
    // AUDIT FIX (2026-08-24): token + global_id were missing from deps — mounting
        // before auth resolved left the widget spinning forever ("GENERATING
        // HEATMAP..."), and selecting a vehicle never refetched its heatmap.
        }, [feed_id, global_id, hours, token, width, height]);

    return (
        <div className="relative matrix-card overflow-hidden bg-black aspect-video flex items-center justify-center">
            {loading && <div className="absolute inset-0 z-10 flex items-center justify-center font-lcd text-xs animate-pulse bg-black/50">GENERATING HEATMAP...</div>}
            <canvas 
                ref={canvasRef} 
                width={width} 
                height={height}
                className="w-full h-full object-contain opacity-80"
            />
            <div className="absolute bottom-2 right-2 flex items-center gap-1 text-[10px] font-lcd opacity-40 bg-black px-1">
                <Camera size={10} /> {feed_id || "GLOBAL GRID"}
            </div>
        </div>
    );
};
