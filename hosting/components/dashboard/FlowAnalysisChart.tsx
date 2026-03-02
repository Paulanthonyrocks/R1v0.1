// components/dashboard/FlowAnalysisChart.tsx
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
import { TrendDataPoint } from '@/lib/types'; // Ensure type import

interface FlowAnalysisChartProps {
  data: TrendDataPoint[];
  isLoading?: boolean;
  timeRange: 'day' | 'week' | 'month';
}

// Helper to format timestamp for XAxis based on the range
const formatXAxis = (tickItem: string, timeRange: 'day' | 'week' | 'month') => {
  try {
    const date = new Date(tickItem);
    if (isNaN(date.getTime())) return String(tickItem); // Handle invalid date strings
    switch (timeRange) {
      case 'day':
        return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
      case 'week':
        return date.toLocaleDateString('en-US', { weekday: 'short', day: 'numeric' });
      case 'month':
        return date.toLocaleDateString('en-US', { month: 'numeric', day: 'numeric' });
      default:
        return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    }
  } catch (e) {
    console.error("Error formatting date:", tickItem, e);
    return String(tickItem); // Fallback
  }
};

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ name: string; value: number; color: string; dataKey: string }>;
  label?: string;
  timeRange: 'day' | 'week' | 'month';
}

// Custom Tooltip Component
const CustomTooltipComponent = ({ active, payload, label, timeRange }: CustomTooltipProps) => {
    if (active && payload && payload.length && label) {
      return (
        <div className="bg-background/90 border border-border p-2 rounded-md shadow-lg text-xs backdrop-blur-sm">
          <p className="label text-muted-foreground">{`Time: ${formatXAxis(label, timeRange)}`}</p>
          {payload.map((entry: { name: string; value: number; color: string; dataKey: string }, index: number) => (
             <p key={`item-${index}`} style={{ color: entry.color }} className="font-medium">
                {`${entry.name}: ${entry.value?.toFixed(1)} ${entry.dataKey === 'avg_speed' ? 'km/h' : (entry.dataKey === 'congestion_index' || entry.dataKey === 'health_score') ? '%' : ''}`}
             </p>
          ))}
        </div>
      );
    }
    return null;
};

const FlowAnalysisChart = ({ data, isLoading, timeRange }: FlowAnalysisChartProps) => {
    // --- Data Aggregation ---\
    const aggregateData = (data: TrendDataPoint[], timeRange: 'day' | 'week' | 'month') => {
      if (timeRange === 'day') return data; // No aggregation for daily data

      const interval: 'hour' | 'day' = timeRange === 'week' ? 'hour' : 'day';
      const aggregatedData: TrendDataPoint[] = [];
      const groups: { [key: string]: TrendDataPoint[] } = {};

      data.forEach(item => {
        const date = new Date(item.timestamp);
        const key = interval === 'hour'
          ? date.toISOString().slice(0, 13) // Group by hour
          : date.toISOString().slice(0, 10); // Group by day

        if (!groups[key]) {
          groups[key] = [];
        }
        groups[key].push(item);
      });

      for (const key in groups) {
        const group = groups[key];
        const totalVehiclesSum = group.reduce((sum, item) => sum + (item.total_vehicles ?? 0), 0);
        const avgSpeedSum = group.reduce((sum, item) => sum + (item.avg_speed ?? 0), 0);
        const congestionIndexSum = group.reduce((sum, item) => sum + (item.congestion_index ?? 0), 0);
        const healthScoreSum = group.reduce((sum, item) => sum + (item.health_score ?? 100), 0);

        aggregatedData.push({
          timestamp: new Date(key + (interval === 'hour' ? ':00' : '')).toISOString(),
          total_vehicles: totalVehiclesSum / group.length,
          avg_speed: avgSpeedSum / group.length,
          congestion_index: congestionIndexSum / group.length,
          health_score: healthScoreSum / group.length,
        });
      }

      return aggregatedData;
    };

    const aggregatedData = aggregateData(data, timeRange);

    const primaryColor = 'var(--lcd-text)'; 
    const secondaryColor = '#166534'; // dark green
    const tertiaryColor = '#f59e0b'; // amber-500, for congestion
    const mutedColor = 'rgba(0, 0, 0, 0.4)'; 
    const gridColor = 'rgba(0, 0, 0, 0.1)'; 

   if (isLoading) {
     return (
       <div className="flex items-center justify-center h-full text-muted-foreground animate-pulse text-sm">
         Loading Chart Data...
       </div>
     );
   }

   if (!data || data.length === 0) {
        return (
            <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
                No trend data available for the selected period.
            </div>
        );
    }

    return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart
        data={aggregatedData}
        margin={{ top: 10, right: 25, left: -10, bottom: 0 }}
        aria-label={`Flow analysis chart showing vehicle count, average speed, and congestion index over the last ${timeRange}`}
      >
        <CartesianGrid strokeDasharray="3 3" stroke={gridColor} horizontal={true} vertical={false} />

        <XAxis
          dataKey="timestamp"
          stroke={mutedColor}
          fontSize={10}
          tickLine={false}
          axisLine={{ stroke: gridColor }}
          dy={5} 
          tickFormatter={(tick) => formatXAxis(tick, timeRange)}
          minTickGap={20} 
        />

        <YAxis
          yAxisId="left"
          dataKey="total_vehicles"
          stroke={secondaryColor}
          fontSize={10}
          tickLine={false}
          axisLine={false}
          tickFormatter={(value) => `${value}`}
          width={35}
          domain={['auto', (dataMax: number) => Math.max(10, Math.ceil(dataMax * 1.1))]}
        />

        <YAxis
          yAxisId="right"
          orientation="right"
          dataKey="avg_speed"
          stroke={primaryColor}
          fontSize={10}
          tickLine={false}
          axisLine={false}
          tickFormatter={(value) => `${value.toFixed(0)}`}
          width={35}
          domain={[0, 'dataMax + 10']}
        />

        <Tooltip
            content={<CustomTooltipComponent timeRange={timeRange} />}
            cursor={{ stroke: mutedColor, strokeWidth: 1, strokeDasharray: "3 3" }}
            wrapperStyle={{ outline: 'none' }}
        />

        <Legend
            iconSize={10}
            wrapperStyle={{ fontSize: '11px', paddingTop: '10px' }}
            verticalAlign="top"
            align="right"
        />

        <Line
          yAxisId="left"
          type="monotone"
          dataKey="total_vehicles"
          name="Vehicles"
          stroke={secondaryColor}
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 5, strokeWidth: 1, fill: secondaryColor }}
          isAnimationActive={false}
        />

        <Line
          yAxisId="right"
          type="monotone"
          dataKey="avg_speed"
          name="Avg Speed (km/h)"
          stroke={primaryColor}
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 5, strokeWidth: 1, fill: primaryColor }}
          isAnimationActive={false}
        />
        
        <Line
          yAxisId="right"
          type="monotone"
          dataKey="congestion_index"
          name="Congestion (%)"
          stroke={tertiaryColor}
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 5, strokeWidth: 1, fill: tertiaryColor }}
          isAnimationActive={false}
        />

        <Line
          yAxisId="right"
          type="monotone"
          dataKey="health_score"
          name="System Health (%)"
          stroke="#ef4444" // red-500
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 5, strokeWidth: 1, fill: "#ef4444" }}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
};

export default React.memo(FlowAnalysisChart);
