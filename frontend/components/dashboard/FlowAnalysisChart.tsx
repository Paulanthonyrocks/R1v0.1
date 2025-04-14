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

// Custom Tooltip Component
const CustomTooltipComponent = ({ active, payload, label, timeRange }: any) => {
    if (active && payload && payload.length) {
      return (
        <div className="bg-background/90 border border-border p-2 rounded-md shadow-lg text-xs backdrop-blur-sm">
          <p className="label text-muted-foreground">{`Time: ${formatXAxis(label, timeRange)}`}</p>
          {payload.map((entry: any, index: number) => (
             <p key={`item-${index}`} style={{ color: entry.color }} className="font-medium">
                {/* Adjust formatting and units as needed */}
                {`${entry.name}: ${entry.value?.toFixed(1)} ${entry.dataKey === 'avg_speed' ? 'mph' : ''}`}
             </p>
          ))}
        </div>
      );
    }
    return null;
};



const FlowAnalysisChart = ({ data, isLoading, timeRange }: FlowAnalysisChartProps) => {
    // --- Data Aggregation ---
    const aggregateData = (data: TrendDataPoint[], timeRange: 'day' | 'week' | 'month') => {
      if (timeRange === 'day') return data; // No aggregation for daily data

      let interval: 'hour' | 'day' = timeRange === 'week' ? 'hour' : 'day';
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

        aggregatedData.push({
          timestamp: new Date(key + (interval === 'hour' ? ':00' : '')).toISOString(),
          total_vehicles: totalVehiclesSum / group.length,
          avg_speed: avgSpeedSum / group.length,
        });
      }

      return aggregatedData;
    };

    const aggregatedData = aggregateData(data, timeRange);







    // Assuming CSS variables are defined globally
    const primaryColor = 'hsl(var(--primary))';
    const secondaryColor = 'hsl(var(--secondary-foreground))';
    const mutedColor = 'hsl(var(--muted-foreground))';
    const gridColor = 'hsla(var(--border), 0.3)'; // Use border color with alpha

   // Handle loading state
   if (isLoading) {
     return (
       <div className="flex items-center justify-center h-full text-muted-foreground animate-pulse text-sm">
         Loading Chart Data...
       </div>
     );
   }

   // Handle empty data state
   if (!data || data.length === 0) {
        return (
            <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
                No trend data available for the selected period.
            </div>
        );
    }

    // Pre-format timestamps
    const formattedData = aggregatedData.map(item => ({
      ...item,
      formattedTimestamp: formatXAxis(item.timestamp, timeRange),
    }));

  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart
        data={aggregatedData}


        margin={{ top: 10, right: 25, left: -10, bottom: 0 }} // Fine-tuned margins
        aria-label={`Flow analysis chart showing vehicle count and average speed over the last ${timeRange}`}
      >
        <CartesianGrid strokeDasharray="3 3" stroke={gridColor} horizontal={true} vertical={false} />

        <XAxis
          dataKey="formattedTimestamp"
          stroke={mutedColor}
          fontSize={10}
          tickLine={false}
          axisLine={{ stroke: gridColor }}
          dy={5} // Offset labels slightly down
          // interval="preserveStartEnd" // Consider if needed based on data density
          minTickGap={20} // Prevent label overlap
        />

        {/* Left Y Axis - Vehicle Count */}
        <YAxis
          yAxisId="left"
          dataKey="total_vehicles"
          stroke={secondaryColor}
          fontSize={10}
          tickLine={false}
          axisLine={false}
          tickFormatter={(value) => `${value}`} // Simple integer format
          width={35} // Slightly more width for labels
          domain={['auto', (dataMax: number) => Math.max(10, Math.ceil(dataMax * 1.1))]} // Ensure min height, add padding
        />

        {/* Right Y Axis - Average Speed */}
        <YAxis
          yAxisId="right"
          orientation="right"
          dataKey="avg_speed"
          stroke={primaryColor}
          fontSize={10}
          tickLine={false}
          axisLine={false}
          tickFormatter={(value) => `${value.toFixed(0)}`} // Integer mph
          width={35} // Slightly more width for labels
          domain={['auto', 'auto']} // Auto domain usually works well
        />

        <Tooltip
            content={<CustomTooltipComponent timeRange={timeRange} />} // Pass timeRange to tooltip
            cursor={{ stroke: mutedColor, strokeWidth: 1, strokeDasharray: "3 3" }}
            wrapperStyle={{ outline: 'none' }} // Remove potential focus outline from wrapper
        />

        <Legend
            iconSize={10}
            wrapperStyle={{ fontSize: '11px', paddingTop: '10px' }}
            verticalAlign="top" // Position legend at the top
            align="right" // Align to the right
        />

        {/* Line for Vehicle Count - Added Animation Props */}
        <Line
          yAxisId="left"
          type="monotone"
          dataKey="total_vehicles"
          name="Vehicles"
          stroke={secondaryColor}
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 5, strokeWidth: 1, fill: secondaryColor }}
          isAnimationActive={true} // Enable animation
          animationDuration={1000} // Duration in ms
          animationEasing="ease-out" // Animation style
        />

        {/* Line for Average Speed - Added Animation Props */}
        <Line
          yAxisId="right"
          type="monotone"
          dataKey="avg_speed"
          name="Avg Speed (mph)"
          stroke={primaryColor}
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 5, strokeWidth: 1, fill: primaryColor }}
          isAnimationActive={true} // Enable animation
          animationDuration={1000} // Duration in ms
          animationEasing="ease-out"
        />
      </LineChart>
    </ResponsiveContainer>
  );
};

const CustomTooltip = React.memo(CustomTooltipComponent);

export default React.memo(FlowAnalysisChart); // Memoize the component