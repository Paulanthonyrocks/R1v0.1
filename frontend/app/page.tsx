// app/page.tsx
"use client";

import React, { useState, useEffect } from "react";
import MatrixCard from "@/components/MatrixCard";
import MatrixButton from "@/components/MatrixButton";
import {
  Chart as ChartJS,
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

ChartJS.register(
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

const getRandomStatus = () => {
  const statuses = ["online", "offline", "warning"];
  return statuses[Math.floor(Math.random() * statuses.length)];
};

const HomePage = () => {
  const [dataType, setDataType] = useState("trafficFlow");
  const [isLoading, setIsLoading] = useState(true);

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
    <div className="w-full p-4">
      <h1 className="text-2xl mb-4 font-bold uppercase">Traffic Management Dashboard</h1>
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
    </div>
  );
};

export default HomePage;