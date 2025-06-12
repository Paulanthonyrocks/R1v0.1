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
    <AuthGuard requiredRole={UserRole.PLANNER}>
    <div className="container mx-auto p-4">
      {loading && (
        <div className="fixed top-0 left-0 w-full h-full flex items-center justify-center bg-matrix-bg z-50">
          <div className="text-matrix tracking-normal">Loading...</div> {/* Added tracking-normal */}
        </div>
      )}
      <h1 className="text-3xl font-bold mb-4 text-primary tracking-normal">Export Data</h1> {/* Added text-primary tracking-normal */}
      <MatrixCard title="Export Configuration" className="pixel-drop-shadow"> {/* Added pixel-drop-shadow. This MatrixCard itself uses .matrix-card (green bg, black border) */}
        <div className="mb-4">
          <h2 className="text-xl text-primary tracking-normal">Select Format</h2> {/* Added text-primary tracking-normal */}
          <div className="flex space-x-2">
            <MatrixButton 
              onClick={() => handleFormatSelect("CSV")}
              className={cn(selectedFormat === "CSV" && "bg-card text-primary hover:bg-card/80")}
            >
              CSV
            </MatrixButton>
            <MatrixButton 
              onClick={() => handleFormatSelect("JSON")}
              className={cn(selectedFormat === "JSON" && "bg-card text-primary hover:bg-card/80")}
            >
              JSON
            </MatrixButton>
            <MatrixButton 
              onClick={() => handleFormatSelect("XML")}
              className={cn(selectedFormat === "XML" && "bg-card text-primary hover:bg-card/80")}
            >
              XML
            </MatrixButton>
          </div>
        </div>
        <div className="mb-4">
          <h2 className="text-xl text-primary tracking-normal">Select Data</h2> {/* Added text-primary tracking-normal */}
          <div className="flex space-x-2">
            <MatrixButton
              onClick={() => handleDataSelect("Traffic Data")}
              className={cn(selectedData.includes("Traffic Data") && "bg-card text-primary hover:bg-card/80")}
            >
              Traffic Data
            </MatrixButton>
            <MatrixButton
              onClick={() => handleDataSelect("Incidents")}
              className={cn(selectedData.includes("Incidents") && "bg-card text-primary hover:bg-card/80")}
            >
              Incidents
            </MatrixButton>
          </div>
        </div>
        <div className="mb-4">
          <h2 className="text-xl text-primary tracking-normal">Select Time Range</h2> {/* Added text-primary tracking-normal */}
          <div className="flex space-x-2">
            <MatrixButton 
              onClick={() => handleTimeRangeSelect("Last 24h")}
              className={cn(timeRange === "Last 24h" && "bg-card text-primary hover:bg-card/80")}
            >
              Last 24h
            </MatrixButton>
            <MatrixButton 
              onClick={() => handleTimeRangeSelect("Last Week")}
              className={cn(timeRange === "Last Week" && "bg-card text-primary hover:bg-card/80")}
            >
              Last Week
            </MatrixButton>
          </div>
        </div>
        <MatrixButton onClick={handleExport}>Export</MatrixButton> {/* This one remains default */}
        {exportProgress > 0 && (
          <div className="mt-4">
            <div className="matrix-progress-bar"> {/* Styles verified in globals.css: green track, black border, black fill */}
              <div 
                className="matrix-progress-bar__fill"
                data-progress="true"
                {...{ style: { '--progress-value': `${exportProgress}%` } as React.CSSProperties }}
              />
            </div>
            <p className="text-sm mt-1 text-primary tracking-normal"> {/* Added text-primary tracking-normal */}
              {exportProgress === 100
                ? "Export Complete"
                : `Exporting... ${exportProgress}%`}
            </p>
          </div>
        )}
        {dataPreview && (
          <div className="mt-4">
            <h3 className="text-lg text-primary tracking-normal">Data Preview</h3> {/* Added text-primary tracking-normal */}
            <p className="text-matrix-muted-text tracking-normal">{dataPreview}</p> {/* Added tracking-normal */}
          </div>
        )}
      </MatrixCard>
    </div>
    </AuthGuard>
  );
};

export default ExportPage;