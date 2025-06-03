"use client";

import React, { useState } from 'react';
import useSWR from 'swr';
import AuthGuard from "@/components/auth/AuthGuard";
import { UserRole } from "@/lib/auth/roles";
import CongestionNode from '@/components/dashboard/CongestionNode'; // Import CongestionNode
import { AllNodesCongestionResponse, BackendCongestionNodeData } from '@/lib/types'; // Import types

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) {
    const error = new Error('An error occurred while fetching the data.');
    // Attach extra info to the error object.
    (error as any).info = await res.json();
    (error as any).status = res.status;
    throw error;
  }
  return res.json();
};

const NodesPage: React.FC = () => {
  const [searchQuery, setSearchQuery] = useState('');

  // Adjust the API endpoint as necessary. Assuming /api prefix is handled by proxy or base URL.
  const { data: apiResponse, error, isLoading } = useSWR<AllNodesCongestionResponse>('/api/v1/analytics/nodes/congestion', fetcher, {
    refreshInterval: 5000 // Refresh every 5 seconds
  });

  const filteredNodes = apiResponse?.nodes?.filter(node =>
    node.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    node.id.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <AuthGuard requiredRole={UserRole.VIEWER}>
      <div className="p-4">
        <h1 className="text-2xl font-bold mb-6 uppercase text-matrix">Node Congestion Status</h1>

        <div className="mb-6">
          <input
            type="text"
            placeholder="Search nodes by name or ID..."
            className="bg-matrix-panel border border-matrix-border-color text-matrix rounded-md p-2 w-full focus:ring-matrix-green focus:border-matrix-green"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        {isLoading && (
          <div className="flex justify-center items-center h-64">
            <p className="text-matrix text-xl animate-pulse">Loading Node Data...</p>
          </div>
        )}

        {error && (
          <div className="flex justify-center items-center h-64">
            <div className="bg-red-900 border border-red-700 text-red-100 p-4 rounded-md">
              <p className="text-xl font-semibold">Error Loading Nodes</p>
              <p>{(error as any).message || 'Failed to fetch node congestion data.'}</p>
              {(error as any).info?.detail && <p className="text-sm mt-1">Details: {(error as any).info.detail}</p>}
            </div>
          </div>
        )}

        {!isLoading && !error && (!filteredNodes || filteredNodes.length === 0) && (
          <div className="flex justify-center items-center h-64">
            <p className="text-matrix-muted text-lg">
              {apiResponse?.nodes?.length === 0 ? "No nodes are currently reporting data." : "No nodes match your search query."}
            </p>
          </div>
        )}

        {!isLoading && !error && filteredNodes && filteredNodes.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
            {filteredNodes.map((node: BackendCongestionNodeData) => (
              <div key={node.id} className="bg-matrix-panel p-3 rounded-lg border border-matrix-border-color shadow-md hover:shadow-matrix-green/30 transition-shadow">
                <CongestionNode
                  id={node.id}
                  name={node.name}
                  value={node.congestion_score ?? 0} // Default to 0 if score is null/undefined
                  lastUpdated={node.timestamp}
                />
                 {/* Optionally display more details from the node data */}
                 <div className="mt-2 pt-2 border-t border-matrix-border-color/50 text-xs text-matrix-muted-text space-y-0.5">
                    <p title={`Lat: ${node.latitude}, Lon: ${node.longitude}`}>Coords: {node.latitude.toFixed(3)}, {node.longitude.toFixed(3)}</p>
                    {node.average_speed !== null && typeof node.average_speed !== 'undefined' && <p>Avg Speed: {node.average_speed.toFixed(1)} km/h</p>}
                    {node.vehicle_count !== null && typeof node.vehicle_count !== 'undefined' && <p>Vehicles: {node.vehicle_count}</p>}
                 </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </AuthGuard>
  );
};

export default NodesPage;