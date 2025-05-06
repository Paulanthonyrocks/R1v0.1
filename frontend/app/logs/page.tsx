"use client";

import MatrixCard from "@/components/MatrixCard";
import { useState, useEffect } from "react";
import MatrixButton from "@/components/MatrixButton";

const SystemLogsPage = () => {
  const [loading, setLoading] = useState<boolean>(true);
  const [selectedLog, setSelectedLog] = useState<number | null>(null);

  useEffect(() => {
    // Simulate initial data loading
    const timer = setTimeout(() => {
      setLoading(false);
    }, 1000);
    
    return () => clearTimeout(timer);
  }, []);

  const handleLogClick = (index: number) => {
    setSelectedLog(index);
  };

  const closeLogDetails = () => {
    setSelectedLog(null);
  };

  const placeholderLogs = [...Array(5)].map((_, index) => ({
    title: `Log Entry ${index + 1}`,
    description: `This is a placeholder description for log entry ${
      index + 1
    }.`.repeat(2),
    timestamp: new Date().toLocaleString(),
    type: ["Error", "Warning", "Info"][index % 3],
    severity: ["High", "Medium", "Low"][index % 3],
  }));

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <h1 className="text-matrix text-4xl">Loading...</h1>
      </div>
    );
  }

  return (
    <div className="p-4">
      <h1 className="text-2xl font-bold mb-4 text-matrix flex items-center gap-4">
        System Logs
        <input
          type="text"
          placeholder="Search logs..."
          className="bg-transparent border border-matrix-border-color p-2 rounded-md text-matrix-light"
        />
      </h1>

      {/* Advanced Filters */}
      <MatrixCard title="Filters" className="mb-4">
        <div className="flex flex-wrap gap-2">
          <MatrixButton>Type</MatrixButton>
          <MatrixButton>Severity</MatrixButton>
          <MatrixButton>Time</MatrixButton>
        </div>
      </MatrixCard>

      {/* Log List */}
      <div
        className={`grid grid-cols-1 gap-4 ${
          selectedLog !== null ? "pointer-events-none opacity-50" : ""
        }`}
      >
        {placeholderLogs.map((log, index) => (
          <div key={index} onClick={() => handleLogClick(index)}>
            <MatrixCard
              title={log.title}
              className="bg-matrix-panel text-matrix-light cursor-pointer"
            >
              <div className="flex flex-col">
                <p className="text-matrix-muted-text text-sm line-clamp-3">
                  Description: {log.description}
                </p>
                <p className="text-matrix-muted-text text-xs mt-2">
                  Timestamp: {log.timestamp}
                </p>
              </div>
            </MatrixCard>
          </div>
        ))}
      </div>

      {/* Log Details */}
      {selectedLog !== null && (
        <div className="fixed inset-0 flex items-center justify-center bg-black bg-opacity-50 z-50">
          <div className="bg-matrix-panel p-6 rounded-lg relative w-[80%] h-[80%]">
            <MatrixButton
              onClick={closeLogDetails}
              className="absolute top-2 right-2"
            >
              Close
            </MatrixButton>
            <h2 className="text-lg font-bold mb-4 text-matrix">
              {placeholderLogs[selectedLog].title} Details
            </h2>
            <div className="flex flex-col gap-2">
              <p className="text-matrix-muted-text text-sm">
                Type: {placeholderLogs[selectedLog].type}
              </p>
              <p className="text-matrix-muted-text text-sm">
                Severity: {placeholderLogs[selectedLog].severity}
              </p>
              <p className="text-matrix-muted-text text-sm">
                Description: {placeholderLogs[selectedLog].description}
              </p>
              <p className="text-matrix-muted-text text-xs">
                Timestamp: {placeholderLogs[selectedLog].timestamp}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default SystemLogsPage;
