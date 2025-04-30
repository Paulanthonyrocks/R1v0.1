// app/page.tsx
"use client";

import React, { useState, useEffect, useRef } from "react";
import MatrixCard from "@/components/MatrixCard";
import MatrixButton from "@/components/MatrixButton";
import {
  Chart,
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend,
  ArcElement,
} from "chart.js";
import { Bar, Doughnut } from "react-chartjs-2";
import dynamic from "next/dynamic";

Chart.register(
  CategoryScale,
  LinearScale,
  BarElement,
  ArcElement,
  Title,
  Tooltip,
  Legend
);

const Map = dynamic(() => import("./Map"), {
  ssr: false,
  loading: () => <p>Loading map...</p>,
});

const AnimatedText = ({ text }: { text: string }) => {
  return <span className="matrix-animated-text">{text}</span>;
};

const ProgressBar = ({ progress }: { progress: number }) => {
  return (
    <div className="relative pt-1">
      <div className="overflow-hidden h-2 mb-4 text-xs flex rounded bg-matrix-dark">
        <div
          style={{ width: `${progress}%` }}
          className="shadow-none flex flex-col text-center whitespace-nowrap text-white justify-center bg-matrix"
        ></div>
      </div>
    </div>
  );
};

const getRandomData = (length: number) => {
  return Array.from({ length }, () => Math.floor(Math.random() * 100));
};

const HomePage = () => {
  const [dataType, setDataType] = useState("trafficFlow");
  const [isLoading, setIsLoading] = useState(true);
  const [matrixLines, setMatrixLines] = useState<string[]>([]);
  const matrixContainerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
      const numLines = 30;
      const newLines = Array.from({ length: numLines }, () => {
        const lineLength = Math.floor(Math.random() * 20) + 5;
      return Array.from({ length: lineLength }, () =>
        Math.random() < 0.5 ? "0" : "1"
      ).join("");
    });
    setMatrixLines(newLines);

    const intervalId = setInterval(() => {
      setMatrixLines((prevLines) => {
        const updatedLines = prevLines.map((line) => {
          const lineLength = line.length;
          return Array.from({ length: lineLength }, () =>
            Math.random() < 0.5 ? "0" : "1"
          ).join("");
        });
        return updatedLines;
      });
    }, 500);
    return () => clearInterval(intervalId);
  }, []);

  useEffect(() => {
    const timeoutId = setTimeout(() => {
      setIsLoading(false);
    }, 2000);

    return () => clearTimeout(timeoutId);
  }, []);

  const data = {
    labels: ["Area A", "Area B", "Area C", "Area D", "Area E", "Area F"],
    datasets: [
      {
        label: "Traffic Volume",
        data: getRandomData(6),
        backgroundColor: "hsl(140, 80%, 45%)",
      },
    ],
  };

  const doughnutData = {
    labels: ["Online", "Offline", "Warning"],
    datasets: [
      {
        label: "Signal Status",
        data: getRandomData(3),
        backgroundColor: ["hsl(140, 80%, 45%)", "hsl(0, 80%, 45%)", "hsl(39, 100%, 50%)"],
      },
    ],
  };

  if (isLoading) {
    return (
      <div className="w-full h-screen flex justify-center items-center">
        <div className="loader"></div>
      </div>
    );
  }
  return (
    <div className="h-screen bg-matrix-bg flex flex-col items-center justify-center overflow-hidden relative">
      <div
        ref={matrixContainerRef}
        className="absolute inset-0 z-0 pointer-events-none overflow-hidden"
        aria-hidden="true"
      >
        <div className="absolute inset-0 grid grid-cols-30 animate-matrix gap-2 font-matrix">
          {matrixLines.map((line, index) => (
            <div key={index} className="text-matrix-text text-sm">
              {line}
            </div>
          ))}
        </div>
      </div>
      <main className="relative z-10 w-full p-4">        <h1 className="text-2xl mb-4 font-bold uppercase">Traffic Management Dashboard</h1>
          <div className="grid grid-cols-1 md:grid-cols-1 gap-4">
            <MatrixCard title="World Map" colorOverride="hsl(240, 80%, 45%)">
            <div className="h-[300px] w-full">


            <Map />
          </div>
        </MatrixCard>
        <div className="flex gap-2">
          <MatrixButton onClick={() => setDataType("trafficFlow")}>Traffic Flow</MatrixButton>
          <MatrixButton onClick={() => setDataType("activeIncidents")}>Active Incidents</MatrixButton>
          <MatrixButton onClick={() => setDataType("signalStatus")}>Signal Status</MatrixButton>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {dataType === "trafficFlow" && (
            <MatrixCard title="Traffic Flow" colorOverride="hsl(140, 80%, 45%)">
              <Bar data={data} />
            </MatrixCard>
          )}
          {dataType === "activeIncidents" && (
            <MatrixCard title="Active Incidents" colorOverride="hsl(0, 80%, 45%)">
              <div className="space-y-2">
                <AnimatedText text="Incident Detected: Road Blockage" />
                <AnimatedText text="Incident Detected: Road blocked due to a car crash." />
              </div>
            </MatrixCard>
          )}
          {dataType === "signalStatus" && (
            <MatrixCard title="Signal Status" colorOverride="hsl(39, 100%, 50%)">
              <div className="space-y-2">
                <ProgressBar progress={Math.random() * 100} />
                <ProgressBar progress={Math.random() * 100} />
                <ProgressBar progress={Math.random() * 100} />
              </div>
              <Doughnut data={doughnutData} />
            </MatrixCard>
          )}
        </div>
      </div>
      </main>
    </div>        
  );
};

export default HomePage;