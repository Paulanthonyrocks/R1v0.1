"use client";

import { useState, useEffect } from "react";
import MatrixCard from "@/components/MatrixCard";
import AuthGuard from "@/components/auth/AuthGuard";
import MatrixButton from "@/components/MatrixButton";
import { UserRole } from "@/lib/auth/roles";

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
    // Reset preview when format changes
    setDataPreview("");
  };

  const handleDataSelect = (data: string) => {
    setSelectedData((prev) =>
      prev.includes(data) ? prev.filter((d) => d !== data) : [...prev, data]
    );
  };

  const handleTimeRangeSelect = (range: string) => {
    setTimeRange(range);
    // Reset preview when time range changes
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
    <AuthGuard requiredRole={UserRole.OPERATOR}>
    <div className="container mx-auto p-4">
      {loading && (
        <div className="fixed top-0 left-0 w-full h-full flex items-center justify-center bg-matrix-bg z-50">
          <div className="text-matrix">Loading...</div>
        </div>
      )}
      <h1 className="text-3xl font-bold mb-4">Export Data</h1>
      <MatrixCard title="Export Configuration">
        <div className="mb-4">
          <h2 className="text-xl">Select Format</h2>
          <div className="flex space-x-2">
            <MatrixButton 
              onClick={() => handleFormatSelect("CSV")}
              backgroundColor={selectedFormat === "CSV" ? "var(--matrix)" : ""}
            >
              CSV
            </MatrixButton>
            <MatrixButton 
              onClick={() => handleFormatSelect("JSON")}
              backgroundColor={selectedFormat === "JSON" ? "var(--matrix)" : ""}
            >
              JSON
            </MatrixButton>
            <MatrixButton 
              onClick={() => handleFormatSelect("XML")}
              backgroundColor={selectedFormat === "XML" ? "var(--matrix)" : ""}
            >
              XML
            </MatrixButton>
          </div>
        </div>
        <div className="mb-4">
          <h2 className="text-xl">Select Data</h2>
          <div className="flex space-x-2">
            <MatrixButton
              onClick={() => handleDataSelect("Traffic Data")}
              backgroundColor={
                selectedData.includes("Traffic Data") ? "var(--matrix)" : ""
              }
            >
              Traffic Data
            </MatrixButton>
            <MatrixButton
              onClick={() => handleDataSelect("Incidents")}
              backgroundColor={
                selectedData.includes("Incidents") ? "var(--matrix)" : ""
              }
            >
              Incidents
            </MatrixButton>
          </div>
        </div>
        <div className="mb-4">
          <h2 className="text-xl">Select Time Range</h2>
          <div className="flex space-x-2">
            <MatrixButton 
              onClick={() => handleTimeRangeSelect("Last 24h")}
              backgroundColor={timeRange === "Last 24h" ? "var(--matrix)" : ""}
            >
              Last 24h
            </MatrixButton>
            <MatrixButton 
              onClick={() => handleTimeRangeSelect("Last Week")}
              backgroundColor={timeRange === "Last Week" ? "var(--matrix)" : ""}
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
            <p className="text-sm mt-1">
              {exportProgress === 100
                ? "Export Complete"
                : `Exporting... ${exportProgress}%`}
            </p>
          </div>
        )}
        {dataPreview && (
          <div className="mt-4">
            <h3 className="text-lg">Data Preview</h3>
            <p className="text-matrix-muted-text">{dataPreview}</p>
          </div>
        )}
      </MatrixCard>
    </div>
    </AuthGuard>
  );
};

export default ExportPage;