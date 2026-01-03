"use client";

import React from 'react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts';

interface PredictiveFlowChartProps {
  data: any[];
  isLoading?: boolean;
}

const PredictiveFlowChart = ({ data, isLoading }: PredictiveFlowChartProps) => {
    const primaryColor = 'var(--lcd-text)'; 
    const forecastedColor = '#f59e0b'; // amber-500
    const actualColor = '#166534'; // dark green
    const mutedColor = 'rgba(0, 0, 0, 0.4)'; 
    const gridColor = 'rgba(0, 0, 0, 0.1)'; 

   if (isLoading) {
     return (
       <div className="flex items-center justify-center h-64 text-muted-foreground animate-pulse text-sm font-lcd">
         SYNCING FORECAST DATA...
       </div>
     );
   }

   if (!data || data.length === 0) {
        return (
            <div className="flex items-center justify-center h-64 text-muted-foreground text-sm font-lcd border border-dashed border-lcd-text/20">
                NO COMPARISON DATA LOGGED FOR THIS PERIOD
            </div>
        );
    }

    return (
    <ResponsiveContainer width="100%" height={400}>
      <LineChart
        data={data}
        margin={{ top: 10, right: 30, left: 0, bottom: 0 }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke={gridColor} horizontal={true} vertical={false} />

        <XAxis
          dataKey="timestamp"
          stroke={mutedColor}
          fontSize={10}
          tickLine={false}
          axisLine={{ stroke: gridColor }}
          tickFormatter={(tick) => new Date(tick).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        />

        <YAxis
          stroke={mutedColor}
          fontSize={10}
          tickLine={false}
          axisLine={false}
          tickFormatter={(value) => `${value}%`}
        />

        <Tooltip
            contentStyle={{ backgroundColor: 'rgba(0,0,0,0.8)', border: '1px solid var(--lcd-text)', borderRadius: '0', fontSize: '10px' }}
            itemStyle={{ color: 'var(--lcd-text)' }}
            labelStyle={{ color: 'rgba(255,255,255,0.5)', marginBottom: '4px' }}
            labelFormatter={(label) => `TIME: ${new Date(label).toLocaleString()}`}
        />

        <Legend
            verticalAlign="top"
            align="right"
            wrapperStyle={{ fontSize: '10px', paddingBottom: '20px' }}
        />

        <Line
          type="monotone"
          dataKey="forecasted"
          name="FORECASTED (%)"
          stroke={forecastedColor}
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
        />

        <Line
          type="monotone"
          dataKey="actual"
          name="ACTUAL (%)"
          stroke={actualColor}
          strokeWidth={2}
          strokeDasharray="5 5"
          dot={false}
          activeDot={{ r: 4 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
};

export default PredictiveFlowChart;
