// frontend/components/nodes/NodeCard.tsx
import React from 'react';
import { BackendCongestionNodeData } from '@/lib/types';
import CongestionNode from '@/components/dashboard/CongestionNode'; // Adjusted path

interface NodeCardProps {
  node: BackendCongestionNodeData;
}

const NodeCard: React.FC<NodeCardProps> = ({ node }) => {
  return (
    <div className="bg-matrix-panel p-3 rounded-lg border border-matrix-border-color shadow-md hover:shadow-matrix-green/30 transition-shadow h-full flex flex-col"> {/* Added h-full and flex flex-col */}
      <CongestionNode
        id={node.id}
        name={node.name}
        value={node.congestion_score ?? 0}
        lastUpdated={node.timestamp}
      />
      {/* Display more details from the node data */}
      <div className="mt-2 pt-2 border-t border-matrix-border-color/50 text-xs text-matrix-muted-text space-y-0.5 flex-grow"> {/* Added flex-grow */}
        <p title={`Lat: ${node.latitude}, Lon: ${node.longitude}`}>
          Coords: {node.latitude.toFixed(3)}, {node.longitude.toFixed(3)}
        </p>
        {node.average_speed !== null && typeof node.average_speed !== 'undefined' && (
          <p>Avg Speed: {node.average_speed.toFixed(1)} km/h</p>
        )}
        {node.vehicle_count !== null && typeof node.vehicle_count !== 'undefined' && (
          <p>Vehicles: {node.vehicle_count}</p>
        )}
      </div>
    </div>
  );
};

export default NodeCard;
