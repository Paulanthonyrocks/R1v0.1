"use client";

import React, { useState, useEffect } from 'react'; // Added useEffect
// import useSWR from 'swr'; // Removed useSWR
import AuthGuard from "@/components/auth/AuthGuard";
import { UserRole } from "@/lib/auth/roles";
// CongestionNode is now used within NodeCard
import { BackendCongestionNodeData } from '@/lib/types'; // No longer need AllNodesCongestionResponse from SWR
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates'; // Import the hook
import NodeCard from '@/components/nodes/NodeCard'; // Import the new NodeCard component
import { AlertTriangle } from 'lucide-react'; // Import error icon

// const fetcher = async (url: string) => { ... }; // Removed fetcher

const NodesPage: React.FC = () => {
  const [searchQuery, setSearchQuery] = useState('');

  const {
    nodeCongestionData,
    isConnected,
    isReady,
    error: wsError,
    startWebSocket
  } = useRealtimeUpdates(); // Use the hook

  useEffect(() => {
    // Start WebSocket connection on component mount
    if (!isConnected) { // Optionally check if already connected
        console.log("NodesPage: Attempting to start WebSocket connection.");
        startWebSocket();
    }
  }, [startWebSocket, isConnected]);

  const filteredNodes = nodeCongestionData?.filter(node =>
    node.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    node.id.toLowerCase().includes(searchQuery.toLowerCase())
  );

  // Determine loading state based on WebSocket connection and data presence
  const isLoading = !isReady || !isConnected; // Consider loading if not ready or not connected
  const displayError = wsError; // Use error from WebSocket hook

  return (
    <AuthGuard requiredRole={UserRole.VIEWER}>
      <div className="p-4">
        <h1 className="text-2xl font-bold mb-6 uppercase text-matrix tracking-normal">Node Congestion Status</h1> {/* Added tracking-normal */}

        <div className="mb-6">
          <input
            type="text"
            placeholder="Search nodes by name or ID..."
            className="bg-matrix-panel border border-matrix-border-color text-matrix rounded-md p-2 w-full focus:ring-primary focus:border-primary tracking-normal placeholder:text-primary" // Added placeholder:text-primary
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        {isLoading && (
          <div className="flex justify-center items-center h-64">
            <p className="text-matrix text-xl animate-pulse tracking-normal">Connecting to Node Data Stream...</p> {/* Added tracking-normal */}
          </div>
        )}

        {displayError && (
          <div className="flex justify-center items-center h-64">
            {/* Changed error panel styling */}
            <div className="bg-card text-primary border border-primary p-4 rounded-md max-w-md w-full pixel-drop-shadow"> {/* Added pixel-drop-shadow */}
              <div className="flex items-center mb-2">
                <AlertTriangle className="h-6 w-6 text-primary mr-2 flex-shrink-0" />
                <p className="text-xl font-semibold tracking-normal">Error Connecting to Node Stream</p> {/* Added tracking-normal */}
              </div>
              <p className="text-sm tracking-normal ml-8">{String(displayError.message || displayError)}</p> {/* Added tracking-normal and left margin for alignment with title */}
            </div>
          </div>
        )}

        {!isLoading && !displayError && (!filteredNodes || filteredNodes.length === 0) && (
          <div className="flex justify-center items-center h-64">
            <p className="text-matrix-muted text-lg tracking-normal"> {/* Added tracking-normal */}
              {nodeCongestionData?.length === 0 ? "No nodes are currently reporting data via WebSocket." : "No nodes match your search query."}
            </p>
          </div>
        )}

        {!isLoading && !displayError && filteredNodes && filteredNodes.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
            {filteredNodes.map((node: BackendCongestionNodeData) => (
              <NodeCard key={node.id} node={node} />
            ))}
          </div>
        )}
      </div>
    </AuthGuard>
  );
};

export default NodesPage;