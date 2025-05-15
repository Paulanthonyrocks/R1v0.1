"use client";

import React, { useState } from 'react';
import MatrixCard from '@/components/MatrixCard';
import AuthGuard from "@/components/auth/AuthGuard";
import { UserRole } from "@/lib/auth/roles";
import MatrixButton from '@/components/MatrixButton';

const NodesPage: React.FC = () => {
  const [isLoading, setIsLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');

  // Simulate loading data
  React.useEffect(() => {
    const timer = setTimeout(() => {
      setIsLoading(false);
    }, 1500);
    return () => clearTimeout(timer);
  }, []);

  return (
    <AuthGuard requiredRole={UserRole.VIEWER}>
      <div className="p-4">
        <h1 className="text-2xl font-bold mb-4 uppercase text-matrix">Connected Nodes</h1>
        {isLoading ? (
          <div className="flex justify-center items-center h-64">
            <p className="text-matrix">Loading...</p>
          </div>
        ) : (
          <>
            <div className="mb-4">
              <input
                type="text"
                placeholder="Search nodes..."
                className="bg-matrix-panel border border-matrix-border-color text-matrix rounded-md p-2 w-full"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {/* Placeholder Nodes */}
              {Array.from({ length: 5 }).map((_, index) => (
                <MatrixCard
                  key={index}
                  title={`Node ${index + 1}`}
                >
                  <div className='flex flex-col'>
                    <p className="text-sm text-matrix-muted-text">
                      Description: Node {index + 1} is a placeholder node.
                    </p>
                    <p className="text-sm text-matrix-muted-text">
                      Status: <span className='text-matrix'>Active</span>
                    </p>
                  </div>
                  <div className="mt-2 flex space-x-2"> {/* Added flex and space-x-2 for button layout */}
                    <MatrixButton
                      className="bg-matrix-dark text-matrix" // Changed addClassName to className
                      onClick={() => console.log("View Details")}
                    >
                      View Details
                    </MatrixButton>
                    <MatrixButton
                      className="bg-matrix-dark text-matrix" // Changed addClassName to className
                      onClick={() => console.log("Restart")}
                    >
                      Restart
                    </MatrixButton>
                    <MatrixButton
                      className="bg-matrix-dark text-matrix" // Changed addClassName to className
                      onClick={() => console.log("Disconnect")}
                    >
                      Disconnect
                    </MatrixButton>
                  </div>
                </MatrixCard>
              ))}
            </div>
          </>
        )}
      </div>
    </AuthGuard>
  );
};

export default NodesPage;