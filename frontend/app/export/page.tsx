"use client";

import { useState, useEffect } from "react";
import MatrixCard from "@/components/MatrixCard";
import MatrixButton from "@/components/MatrixButton";

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
  };

  const handleDataSelect = (data: string) => {
    setSelectedData((prev) =>
      prev.includes(data) ? prev.filter((d) => d !== data) : [...prev, data]
    );
  };

  const handleTimeRangeSelect = (range: string) => {
    setTimeRange(range);
  };

  const handleExport = () => {
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
      setDataPreview("This is a preview of the exported data");
    }, 1000);
  };

  return (
    <div className="container mx-auto p-4">
      {loading && (
        <div className="fixed top-0 left-0 w-full h-full flex items-center justify-center bg-matrix-bg z-50">
          <div className="text-matrix">Loading...</div>
        </div>
      )}
      <h1 className="text-3xl font-bold mb-4">Export Data</h1>
      <MatrixCard>
        <div className="mb-4">
          <h2 className="text-xl">Select Format</h2>
          <div className="flex space-x-2">
            <MatrixButton onClick={() => handleFormatSelect("CSV")}>
              CSV
            </MatrixButton>
            <MatrixButton onClick={() => handleFormatSelect("JSON")}>
              JSON
            </MatrixButton>
            <MatrixButton onClick={() => handleFormatSelect("XML")}>
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
            <MatrixButton onClick={() => handleTimeRangeSelect("Last 24h")}>
              Last 24h
            </MatrixButton>
            <MatrixButton onClick={() => handleTimeRangeSelect("Last Week")}>
              Last Week
            </MatrixButton>
          </div>
        </div>
        <MatrixButton onClick={handleExport}>Export</MatrixButton>
        {exportProgress > 0 && (
          <div className="mt-4">
            <div className="h-4 bg-matrix-panel rounded-full overflow-hidden">
              <div
                className="h-full bg-matrix"
                style={{ width: `${exportProgress}%` }}
              ></div>
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
  );
};

export default ExportPage;
        </div>
      </MatrixCard>
    </div>
  );
};

export default ExportPage;