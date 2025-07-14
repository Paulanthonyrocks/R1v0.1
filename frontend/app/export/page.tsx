"use client";

import { useState, useEffect } from "react";
import MatrixCard from "@/components/MatrixCard";
import AuthGuard from "@/components/auth/AuthGuard";
import MatrixButton from "@/components/MatrixButton";
import { UserRole } from "@/lib/auth/roles";
import { cn } from "@/lib/utils";
import { Signal, Clock, BatteryFull } from 'lucide-react';

const ExportPage: React.FC = () => {
  const [loading, setLoading] = useState<boolean>(true);
  const [selectedFormat, setSelectedFormat] = useState<string>("CSV");
  const [selectedData, setSelectedData] = useState<string[]>([]);
  const [timeRange, setTimeRange] = useState<string>("Last 24h");
  const [exportProgress, setExportProgress] = useState<number>(0);
  const [dataPreview, setDataPreview] = useState<string>("");

  useEffect(() => {
    setTimeout(() => {
      setLoading(false);
    }, 500);
  }, []);

  const handleFormatSelect = (format: string) => {
    setSelectedFormat(format);
    setDataPreview("");
  };

  const handleDataSelect = (data: string) => {
    setSelectedData((prev) =>
      prev.includes(data) ? prev.filter((d) => d !== data) : [...prev, data]
    );
  };

  const handleTimeRangeSelect = (range: string) => {
    setTimeRange(range);
    setDataPreview("");
  };

  const handleExport = () => {
    if (selectedData.length === 0) {
      setDataPreview("Please select data to export");
      return;
    }

    setExportProgress(0);
    const interval = setInterval(() => {
      setExportProgress((prev) => {
        if (prev >= 100) {
          clearInterval(interval);
          return 100;
        }
        return prev + 10;
      });
    }, 300);

    setTimeout(() => {
      setDataPreview(`Preview of ${selectedData.join(", ")} data in ${selectedFormat} format for ${timeRange}`);
    }, 1000);
  };

  return (
    <AuthGuard requiredRole={UserRole.PLANNER}>
      <div className="bg-lcd-bg text-lcd-text font-lcd flex flex-col min-h-screen w-full">
        {/* Status Bar */}
        <header className="flex items-center justify-between px-4 py-1 border-b-2 border-lcd-text">
          <div className="flex items-center space-x-2">
            <Signal size={20} />
            <span className="font-lcd matrix-glow">EXPORT</span>
          </div>
          <div className="flex items-center space-x-2">
            <Clock size={20} />
            <span className="font-lcd matrix-glow">{new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
            <BatteryFull size={20} />
          </div>
        </header>
        <main className="flex-1 p-4">
          {loading && (
            <div className="fixed top-0 left-0 w-full h-full flex items-center justify-center bg-lcd-bg z-50">
              <div className="text-lcd-text tracking-normal">Loading...</div>
            </div>
          )}
          <h1 className="text-3xl font-bold mb-4 text-lcd-text tracking-normal">Export Data</h1>
          <MatrixCard title="Export Configuration" className="pixel-drop-shadow">
            <div className="mb-4">
              <h2 className="text-xl text-lcd-text tracking-normal">Select Format</h2>
              <div className="flex space-x-2">
                <MatrixButton
                  onClick={() => handleFormatSelect("CSV")}
                  className={cn(selectedFormat === "CSV" && "bg-lcd-text text-lcd-bg hover:bg-lcd-text/80")}
                >
                  CSV
                </MatrixButton>
                <MatrixButton
                  onClick={() => handleFormatSelect("JSON")}
                  className={cn(selectedFormat === "JSON" && "bg-lcd-text text-lcd-bg hover:bg-lcd-text/80")}
                >
                  JSON
                </MatrixButton>
                <MatrixButton
                  onClick={() => handleFormatSelect("XML")}
                  className={cn(selectedFormat === "XML" && "bg-lcd-text text-lcd-bg hover:bg-lcd-text/80")}
                >
                  XML
                </MatrixButton>
              </div>
            </div>
            <div className="mb-4">
              <h2 className="text-xl text-lcd-text tracking-normal">Select Data</h2>
              <div className="flex space-x-2">
                <MatrixButton
                  onClick={() => handleDataSelect("Traffic Data")}
                  className={cn(selectedData.includes("Traffic Data") && "bg-lcd-text text-lcd-bg hover:bg-lcd-text/80")}
                >
                  Traffic Data
                </MatrixButton>
                <MatrixButton
                  onClick={() => handleDataSelect("Incidents")}
                  className={cn(selectedData.includes("Incidents") && "bg-lcd-text text-lcd-bg hover:bg-lcd-text/80")}
                >
                  Incidents
                </MatrixButton>
              </div>
            </div>
            <div className="mb-4">
              <h2 className="text-xl text-lcd-text tracking-normal">Select Time Range</h2>
              <div className="flex space-x-2">
                <MatrixButton
                  onClick={() => handleTimeRangeSelect("Last 24h")}
                  className={cn(timeRange === "Last 24h" && "bg-lcd-text text-lcd-bg hover:bg-lcd-text/80")}
                >
                  Last 24h
                </MatrixButton>
                <MatrixButton
                  onClick={() => handleTimeRangeSelect("Last Week")}
                  className={cn(timeRange === "Last Week" && "bg-lcd-text text-lcd-bg hover:bg-lcd-text/80")}
                >
                  Last Week
                </MatrixButton>
              </div>
            </div>
            <MatrixButton onClick={handleExport}>Export</MatrixButton>
            {exportProgress > 0 && (
              <div className="mt-4">
                <div className="matrix-progress-bar">
                  <div
                    className="matrix-progress-bar__fill"
                    data-progress="true"
                    {...{ style: { '--progress-value': `${exportProgress}%` } as React.CSSProperties }}
                  />
                </div>
                <p className="text-sm mt-1 text-lcd-text tracking-normal">
                  {exportProgress === 100
                    ? "Export Complete"
                    : `Exporting... ${exportProgress}%`}
                </p>
              </div>
            )}
            {dataPreview && (
              <div className="mt-4">
                <h3 className="text-lg text-lcd-text tracking-normal">Data Preview</h3>
                <p className="text-lcd-text tracking-normal font-lcd">{dataPreview}</p>
              </div>
            )}
          </MatrixCard>
        </main>
      </div>
    </AuthGuard>
  );
};

export default ExportPage;
