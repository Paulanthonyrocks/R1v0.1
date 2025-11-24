"use client";

import React, { useState, useEffect } from 'react';
import AuthGuard from "@/components/auth/AuthGuard";
import { UserRole } from "@/lib/auth/roles";
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';

// // import NodeCard from '@/components/nodes/NodeCard';
import { AlertTriangle, Signal, Clock, BatteryFull } from 'lucide-react';
// import { BackendCongestionNodeData } from '@/lib/types';

const NodesPage: React.FC = () => {
  const [searchQuery, setSearchQuery] = useState('');
  

  const {
    // nodeCongestionData,
    isConnected,
    isReady,
    error: wsError
  } = useRealtimeUpdates();

  useEffect(() => {
    // Connection is handled by WebSocketProvider.
    console.log(`NodesPage: WebSocket connection status: ${isConnected ? 'Connected' : 'Disconnected'}`);
  }, [isConnected]);

  // const filteredNodes = nodeCongestionData?.filter(node =>
  //   node.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
  //   node.id.toLowerCase().includes(searchQuery.toLowerCase())
  // );

  const isLoading = !isReady || !isConnected;
  const displayError = wsError;

  const renderContent = () => {
    if (isLoading) {
      return (
        <div className="flex justify-center items-center h-64">
          <p className="text-lcd-text text-xl animate-pulse tracking-normal">Connecting to Node Data Stream...</p>
        </div>
      );
    }

    if (displayError) {
      return (
        <div className="flex justify-center items-center h-64">
          <div className="bg-lcd-bg text-red-500 border border-red-500 p-4 rounded-md max-w-md w-full pixel-drop-shadow">
            <div className="flex items-center mb-2">
              <AlertTriangle className="h-6 w-6 text-red-500 mr-2 flex-shrink-0" />
              <p className="text-xl font-semibold tracking-normal">Error Connecting to Node Stream</p>
            </div>
            <p className="text-sm tracking-normal ml-8">{displayError || 'Unknown error'}</p>
          </div>
        </div>
      );
    }

    // if (!filteredNodes || filteredNodes.length === 0) {
    //   return (
    //     <div className="flex justify-center items-center h-64">
    //       <p className="text-lcd-text text-lg tracking-normal">
    //         {nodeCongestionData?.length === 0 ? "No nodes are currently reporting data via WebSocket." : "No nodes match your search query."}
    //       </p>
    //     </div>
    //   );
    // }

    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
        {/* {filteredNodes.map((node: BackendCongestionNodeData) => (
          <NodeCard key={node.id} node={node} />
        ))} */}
      </div>
    );
  };

  return (
    <AuthGuard requiredRole={UserRole.VIEWER}>
      <div className="bg-lcd-bg text-lcd-text font-lcd flex flex-col min-h-screen w-full">
        {/* Status Bar */}
        <header className="flex items-center justify-between px-4 py-1 border-b-2 border-lcd-text">
          <div className="flex items-center space-x-2">
            <Signal size={20} />
            <span className="font-lcd matrix-glow">NODES</span>
          </div>
          <div className="flex items-center space-x-2">
            <Clock size={20} />
            <span className="font-lcd matrix-glow">{new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
            <BatteryFull size={20} />
          </div>
        </header>
        <main className="flex-1 p-4">
          <h1 className="text-2xl font-bold mb-6 uppercase text-lcd-text tracking-normal">Node Congestion Status</h1>

          <div className="mb-6">
            <input
              type="text"
              placeholder="Search nodes by name or ID..."
              className="bg-lcd-bg border border-lcd-text text-lcd-text rounded-md p-2 w-full focus:ring-primary focus:border-primary tracking-normal placeholder:text-lcd-text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>

          {renderContent()}
        </main>
      </div>
    </AuthGuard>
  );
};

export default NodesPage;
