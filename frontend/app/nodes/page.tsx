"use client";

import React, { useState, useMemo } from 'react';
import AuthGuard from "@/components/auth/AuthGuard";
import { UserRole } from "@/lib/auth/roles";
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import NodeCard from '@/components/nodes/NodeCard';
import { AlertTriangle, BatteryFull, Search, MapPin, ArrowLeft } from 'lucide-react';
import Link from 'next/link';
import { BackendCongestionNodeData } from '@/lib/types';

const NodesPage: React.FC = () => {
  const [searchQuery, setSearchQuery] = useState('');
  const { feeds, isConnected, isReady, error: wsError } = useRealtimeUpdates();

  const nodes: BackendCongestionNodeData[] = useMemo(() => {
    return feeds.map(feed => ({
      id: feed.feed_id,
      name: feed.name || feed.feed_id,
      latitude: feed.latitude || feed.config?.latitude || 0,
      longitude: feed.longitude || feed.config?.longitude || 0,
      congestion_score: (feed.latest_metrics?.congestion_score as number) || 0,
      vehicle_count: (feed.latest_metrics?.total_vehicles as number) || 0,
      average_speed: (feed.latest_metrics?.average_speed_kmh as number) || 0,
      timestamp: new Date().toISOString()
    }));
  }, [feeds]);

  const filteredNodes = useMemo(() => {
    return nodes.filter(node =>
      node.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      node.id.toLowerCase().includes(searchQuery.toLowerCase())
    );
  }, [nodes, searchQuery]);

  const isLoading = !isReady || !isConnected;

  const renderContent = () => {
    if (isLoading && nodes.length === 0) {
      return (
        <div className="flex justify-center items-center h-64">
          <p className="text-lcd-text text-xl animate-pulse tracking-normal uppercase font-bold">Establishing Uplink to Nodes...</p>
        </div>
      );
    }

    if (wsError && nodes.length === 0) {
      return (
        <div className="flex justify-center items-center h-64">
          <div className="bg-lcd-bg text-red-500 border border-red-500 p-4 rounded-md max-w-md w-full pixel-drop-shadow">
            <div className="flex items-center mb-2">
              <AlertTriangle className="h-6 w-6 text-red-500 mr-2 flex-shrink-0" />
              <p className="text-xl font-semibold tracking-normal">Telemetry Connection Failure</p>
            </div>
            <p className="text-sm tracking-normal ml-8">{wsError}</p>
          </div>
        </div>
      );
    }

    if (filteredNodes.length === 0) {
      return (
        <div className="flex justify-center items-center h-64 opacity-50">
          <p className="text-lcd-text text-lg tracking-normal uppercase">
            {nodes.length === 0 ? "No active monitoring nodes detected." : "No nodes match the filter criteria."}
          </p>
        </div>
      );
    }

    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6">
        {filteredNodes.map((node) => (
          <NodeCard key={node.id} node={node} />
        ))}
      </div>
    );
  };

  return (
    <AuthGuard requiredRole={UserRole.VIEWER}>
      <div className="bg-lcd-bg text-lcd-text font-lcd flex flex-col min-h-screen w-full">
        {/* Status Bar */}
        <header className="flex items-center justify-between px-4 py-1 border-b-2 border-lcd-text sticky top-0 z-50 bg-lcd-bg">
          <div className="flex items-center gap-4">
              <Link href="/dashboard" className="hover:opacity-70 transition-opacity">
                <ArrowLeft size={20} />
              </Link>
              <div className="flex items-center space-x-2">
                <MapPin size={20} />
                <span className="font-lcd matrix-glow uppercase">Node Topology</span>
              </div>
          </div>
          
          <div className="flex items-center space-x-4">
            <div className="flex items-center gap-2">
                <div className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`}></div>
                <span className="text-[10px] opacity-70">WS {isConnected ? 'LIVE' : 'OFFLINE'}</span>
            </div>
            <span className="font-lcd matrix-glow text-sm">{new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
            <BatteryFull size={20} />
          </div>
        </header>

        <main className="flex-1 p-6 max-w-[1600px] mx-auto w-full">
          <div className="flex flex-col md:flex-row justify-between items-end mb-8 gap-4">
              <div>
                  <h1 className="text-4xl font-bold uppercase tracking-tighter mb-2">Corridor Monitoring</h1>
                  <p className="text-lcd-text/60 max-w-2xl">
                      Individual node telemetry and congestion status. Each node represents a high-frequency camera feed 
                      with edge-processed traffic metrics.
                  </p>
              </div>
              <div className="w-full md:w-96">
                  <div className="relative">
                      <Search className="absolute left-3 top-1/2 -translate-y-1/2 opacity-40" size={18} />
                      <input
                        type="text"
                        placeholder="FILTER NODES..."
                        className="bg-lcd-text/5 border-2 border-lcd-text/20 text-lcd-text rounded-none p-3 pl-10 w-full focus:outline-none focus:border-primary tracking-[0.1em] placeholder:text-lcd-text/30"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                      />
                  </div>
              </div>
          </div>

          <div className="mb-4 flex items-center gap-4 text-[10px] uppercase opacity-60">
              <div className="flex items-center gap-1">
                  <div className="w-2 h-2 bg-green-500"></div> Nominal
              </div>
              <div className="flex items-center gap-1">
                  <div className="w-2 h-2 bg-yellow-500"></div> Moderate
              </div>
              <div className="flex items-center gap-1">
                  <div className="w-2 h-2 bg-red-500"></div> Congested
              </div>
              <div className="ml-auto">
                  Showing {filteredNodes.length} of {nodes.length} nodes
              </div>
          </div>

          {renderContent()}
        </main>
      </div>
    </AuthGuard>
  );
};

export default NodesPage;