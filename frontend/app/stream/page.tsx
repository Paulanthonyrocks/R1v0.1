"use client";

import React, { useState, useEffect } from 'react';
import { Chart } from "react-google-charts";
import MatrixButton from '../../components/MatrixButton';
import MatrixCard from '../../components/MatrixCard';

const StreamPage: React.FC = () => {
  const placeholderData = [
    {
      title: 'Vehicle Count',
      description: 'Detected 150 vehicles in the last minute.',
      timestamp: '2023-11-20 10:00:00',
    },
    {
      title: 'Average Speed',
      description: 'Average speed is 45 km/h.',
      timestamp: '2023-11-20 10:01:00',
    },
    {
      title: 'Incident Reported',
      description: 'Minor accident reported at intersection B.',
      timestamp: '2023-11-20 10:02:00',
    },
    {
      title: 'Traffic Congestion',
      description: 'Traffic congestion detected on Highway A.',
      timestamp: '2023-11-20 10:03:00',
    },
    {
      title: 'Signal Change',
      description: 'Traffic signal changed to red at intersection C.',
      timestamp: '2023-11-20 10:04:00',
    },
  ];

  const dataTypes = ["Vehicle Count", "Average Speed", "Incident Reports", "Traffic Congestion", "Signal Change"];
  const [selectedDataType, setSelectedDataType] = useState<string>("Vehicle Count");
  const [isLoading, setIsLoading] = useState<boolean>(true);

  const handleDataTypeChange = (type: string) => {
    setSelectedDataType(type);
  };

  useEffect(() => {
    const timer = setTimeout(() => {
      setIsLoading(false);
    }, 1000);

    return () => clearTimeout(timer);
  }, []);

  const chartData = [
    ["Time", "Value"],
    ["10:00", 150],
    ["10:01", 45],
    ["10:02", 1],
    ["10:03", 1],
    ["10:04", 1],
  ];

  const chartOptions = {
    title: "Data Stream",
    curveType: "function",
    legend: { position: "bottom" },
    backgroundColor: 'transparent',
    titleTextStyle: { color: 'white' },
    hAxis: {
      textStyle: { color: 'white' },
    },
    vAxis: {
      textStyle: { color: 'white' },
    }
  };

  return (
    <div className="p-4 grid gap-4 grid-cols-1 md:grid-cols-2 lg:grid-cols-3 relative">
      {isLoading && (
        <div className="absolute top-0 left-0 w-full h-full flex justify-center items-center bg-matrix-bg z-50">
          <div className="animate-pulse text-white">Loading...</div>
        </div>
      )}
      <h1 className="text-2xl font-bold col-span-full">
        Real-Time Stream
      </h1>

      <MatrixCard title="Data Type Selection" className="col-span-full">
        <div className="flex flex-wrap gap-2">
          {dataTypes.map((type) => (
            <MatrixButton
              key={type}
              onClick={() => handleDataTypeChange(type)}
              className={`${selectedDataType === type
                ? "bg-matrix"
                : "bg-matrix-dark"
                }`}
            >
              {type}
            </MatrixButton>
          ))}
        </div>
      </MatrixCard>

      <div className="col-span-full">
        <MatrixCard title="Chart">
          <Chart
            chartType="LineChart"
            width="100%"
            height="400px"
            data={chartData}
            options={chartOptions}
          />
        </MatrixCard>
      </div>

      <div className='col-span-full'>
        {placeholderData
          .filter((data) => data.title === selectedDataType)
          .map((data, index) => (
            <MatrixCard
              key={index}
              title={data.title}
              className='animate-fade-in'
            >
              <p className="text-sm">
                {data.description}
              </p>
              <p className="text-xs text-matrix-muted-text mt-2">
                {data.timestamp}
              </p>
            </MatrixCard>
          ))}
      </div>
    </div>
  );
};

export default StreamPage;