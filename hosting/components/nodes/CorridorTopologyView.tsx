"use client";

import React, { useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { BackendCongestionNodeData } from '@/lib/types';
import { Activity, Zap, TrendingUp, AlertTriangle } from 'lucide-react';

interface CorridorTopologyViewProps {
  nodes: BackendCongestionNodeData[];
}

const CorridorTopologyView: React.FC<CorridorTopologyViewProps> = ({ nodes }) => {
  // Sort nodes to form a logical corridor sequence
  // We'll use Latitude as the primary sorting key (North to South or vice versa)
  const sortedNodes = useMemo(() => {
    return [...nodes].sort((a, b) => (b.latitude || 0) - (a.latitude || 0));
  }, [nodes]);

  const getStatusColor = (score: number) => {
    if (score < 30) return '#22c55e'; // green-500
    if (score < 70) return '#eab308'; // yellow-500
    return '#ef4444'; // red-500
  };

  const getStatusBg = (score: number) => {
    if (score < 30) return 'rgba(34, 197, 94, 0.1)';
    if (score < 70) return 'rgba(234, 179, 8, 0.1)';
    return 'rgba(239, 68, 68, 0.1)';
  };

  if (nodes.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-96 border-2 border-dashed border-lcd-text/20 opacity-50">
        <Activity size={48} className="mb-4 animate-pulse" />
        <p className="font-lcd uppercase tracking-widest text-xl">Awaiting Node Telemetry...</p>
      </div>
    );
  }

  return (
    <div className="w-full bg-black/40 border-2 border-lcd-text/10 p-8 relative overflow-hidden min-h-[600px] flex flex-col items-center justify-center rounded-none shadow-2xl">
      {/* Background Matrix Grid Effect */}
      <div className="absolute inset-0 opacity-[0.03] pointer-events-none" 
           style={{ backgroundImage: 'radial-gradient(var(--lcd-text) 1px, transparent 0)', backgroundSize: '40px 40px' }} />
      
      {/* Title / Legend */}
      <div className="absolute top-4 left-4 flex flex-col gap-2 z-10">
        <div className="flex items-center gap-2">
            <TrendingUp size={16} className="text-primary" />
            <span className="text-[10px] font-lcd tracking-widest uppercase opacity-80">Flow Analysis Mode: ACTIVE</span>
        </div>
        <div className="flex items-center gap-4 text-[9px] uppercase tracking-tighter opacity-60">
            <div className="flex items-center gap-1"><div className="w-2 h-2 bg-green-500"></div> NOMINAL</div>
            <div className="flex items-center gap-1"><div className="w-2 h-2 bg-yellow-500"></div> STRESSED</div>
            <div className="flex items-center gap-1"><div className="w-2 h-2 bg-red-500"></div> SATURATED</div>
        </div>
      </div>

      {/* The Central Corridor Path */}
      <div className="relative w-full max-w-4xl py-20 flex flex-col items-center">
        {/* Main Backbone Line */}
        <div className="absolute top-0 bottom-0 w-1 bg-gradient-to-b from-transparent via-lcd-text/20 to-transparent" />
        
        <AnimatePresence>
          {sortedNodes.map((node, index) => {
            const isLast = index === sortedNodes.length - 1;
            const score = node.congestion_score || 0;
            const color = getStatusColor(score);
            const bgColor = getStatusBg(score);

            return (
              <React.Fragment key={node.id}>
                {/* Node Container */}
                <motion.div 
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: index * 0.1 }}
                  className="relative z-10 flex items-center w-full group py-6"
                >
                  {/* Left Side: Node Info Card */}
                  <div className="flex-1 flex justify-end pr-12">
                    <motion.div 
                      whileHover={{ scale: 1.02, x: -5 }}
                      className="w-64 bg-black/80 border-l-4 p-4 matrix-glow-card"
                      style={{ borderLeftColor: color }}
                    >
                      <div className="flex justify-between items-start mb-2">
                        <h3 className="font-lcd text-sm font-bold uppercase truncate max-w-[140px]">{node.name}</h3>
                        <span className="text-[10px] font-mono opacity-50">#{node.id.slice(0,6)}</span>
                      </div>
                      <div className="grid grid-cols-2 gap-2 text-[10px] font-mono">
                        <div className="bg-white/5 p-1 px-2 rounded">
                            <p className="opacity-40 mb-1 uppercase text-[8px]">Vehicles</p>
                            <p className="text-primary font-bold">{node.vehicle_count || 0}</p>
                        </div>
                        <div className="bg-white/5 p-1 px-2 rounded">
                            <p className="opacity-40 mb-1 uppercase text-[8px]">Avg Speed</p>
                            <p className="text-primary font-bold">{Math.round(node.average_speed || 0)} KM/H</p>
                        </div>
                      </div>
                      {score > 70 && (
                        <div className="mt-2 flex items-center gap-1 text-red-500 animate-pulse text-[9px] font-bold uppercase">
                            <AlertTriangle size={10} /> Congestion Detected
                        </div>
                      )}
                    </motion.div>
                  </div>

                  {/* Center Point: The Node Circle */}
                  <div className="relative flex-shrink-0">
                    <motion.div 
                      animate={{ 
                        boxShadow: [
                          `0 0 0px ${color}`, 
                          `0 0 15px ${color}`, 
                          `0 0 0px ${color}`
                        ] 
                      }}
                      transition={{ duration: 2, repeat: Infinity }}
                      className="w-6 h-6 rounded-full border-2 border-black flex items-center justify-center z-20"
                      style={{ backgroundColor: color }}
                    >
                        <div className="w-1.5 h-1.5 bg-black/40 rounded-full" />
                    </motion.div>
                    
                    {/* Pulsing Outer Ring for high congestion */}
                    {score > 50 && (
                        <motion.div 
                            animate={{ scale: [1, 1.8], opacity: [0.5, 0] }}
                            transition={{ duration: 1.5, repeat: Infinity }}
                            className="absolute inset-0 rounded-full z-10"
                            style={{ backgroundColor: color }}
                        />
                    )}
                  </div>

                  {/* Right Side: Visual Metrics / HUD */}
                  <div className="flex-1 pl-12">
                    <div className="flex items-center gap-4">
                        <div className="flex flex-col gap-1">
                            <div className="flex justify-between w-32 text-[8px] opacity-40 uppercase font-lcd">
                                <span>Saturation</span>
                                <span>{score}%</span>
                            </div>
                            <div className="w-32 h-1 bg-white/10 rounded-full overflow-hidden">
                                <motion.div 
                                    initial={{ width: 0 }}
                                    animate={{ width: `${score}%` }}
                                    className="h-full"
                                    style={{ backgroundColor: color }}
                                />
                            </div>
                        </div>
                        <motion.div 
                            whileHover={{ rotate: 90 }}
                            className="p-2 bg-white/5 border border-white/10 rounded-full opacity-40 group-hover:opacity-100 transition-opacity cursor-pointer"
                        >
                            <Zap size={14} />
                        </motion.div>
                    </div>
                  </div>
                </motion.div>

                {/* The Path Between Nodes with "Flow Particles" */}
                {!isLast && (
                  <div className="relative w-1 h-24 flex items-center justify-center">
                    <div className="absolute h-full w-[2px] bg-lcd-text/10" />
                    
                    {/* Animated Particles based on flow (vehicle count) */}
                    {Array.from({ length: Math.min(Math.floor((node.vehicle_count || 0) / 5) + 1, 8) }).map((_, i) => (
                      <motion.div
                        key={i}
                        initial={{ top: '0%', opacity: 0 }}
                        animate={{ 
                            top: ['0%', '100%'], 
                            opacity: [0, 1, 1, 0],
                            scale: [0.8, 1.2, 0.8]
                        }}
                        transition={{ 
                            duration: 2 + Math.random() * 2, 
                            repeat: Infinity, 
                            delay: Math.random() * 2,
                            ease: "linear"
                        }}
                        className="absolute w-1 h-3 rounded-full z-0"
                        style={{ backgroundColor: color }}
                      />
                    ))}
                  </div>
                )}
              </React.Fragment>
            );
          })}
        </AnimatePresence>
      </div>

      {/* Matrix Status Message */}
      <div className="mt-12 text-center opacity-30 group">
          <p className="text-[10px] font-mono tracking-widest uppercase group-hover:opacity-100 transition-opacity">
              Node Topology: [Sync: {new Date().toLocaleTimeString()}] | Stream: STABLE | Latency: 24ms
          </p>
          <div className="flex justify-center gap-2 mt-2">
            {Array.from({ length: 20 }).map((_, i) => (
                <div key={i} className="w-1 h-1 bg-primary rounded-full animate-pulse" style={{ animationDelay: `${i * 0.1}s` }} />
            ))}
          </div>
      </div>
    </div>
  );
};

export default CorridorTopologyView;
