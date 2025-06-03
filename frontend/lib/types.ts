// lib/types.ts
import React from 'react';
import { LucideIcon } from 'lucide-react';

// --- Core Data Types ---
export type SeverityLevel = 'Critical' | 'Warning' | 'Anomaly' | 'INFO' | 'ERROR';

export interface FeedStatusData {
  id: string;
  source: string;
  name?: string;
  status: 'stopped' | 'running' | 'starting' | 'error';
  fps?: number | null;
  error_message?: string | null;
}

export interface AlertData {
  id?: string | number;
  timestamp: string | Date;
  severity: SeverityLevel;
  feed_id?: string | null;
  message: string;
  description?: string;
  location?: string; // This is a simple string; backend might send structured location in details.
  acknowledged?: boolean; // For WebSocket updates on acknowledgement status
  details?: Record<string, any>; // To capture full details from WebSocket if needed
}

export interface TrendDataPoint {
  timestamp: string;
  total_vehicles: number;
  avg_speed: number;
  congestion_index?: number;
}

export interface KpiData {
  average_speed_kmh?: number | null; // Aligned with backend GlobalRealtimeMetrics, made optional to match backend
  congestion_index?: number | null;  // Aligned with backend GlobalRealtimeMetrics, made optional
  active_incidents_count?: number | null; // Aligned with backend GlobalRealtimeMetrics, made optional
  feed_statuses?: { [key: string]: number } | null; // Aligned with backend, e.g. { "running": N, "error": M, "stopped": P }
  total_flow?: number | null; // Already present and matches, made optional
  // Add any other fields from GlobalRealtimeMetrics if needed by the frontend kpis object
  timestamp?: string | Date; // GlobalRealtimeMetrics has a timestamp
  metrics_source?: string | null; // GlobalRealtimeMetrics has this
}

export interface StatCardData {
  id: string;
  title: string;
  value: string;
  change: string;
  changeText: string;
  icon: LucideIcon;
  valueColor?: string;
  changeColor?: string;
}

export interface AlertsResponse {
  alerts: AlertData[];
  total_count: number;
  page: number;
  limit: number;
  total_pages: number;
}

// --- Component Prop Types ---

export interface MatrixCardProps {
 title: string;
 content?: string;
 colorOverride?: string;
 children?: React.ReactNode;
}
export interface StatCardProps  {
  children?: React.ReactNode;
}

export interface AnomalyItemProps extends AlertData {
  onSelect?: (alert: AlertData) => void;
}

export interface CongestionNodeProps {
  id: string;
  name: string;
  value: number;
  lastUpdated?: string;
}

export interface SurveillanceFeedProps extends Omit<FeedStatusData, 'source' | 'error_message'> {
  name: string;
  node: string;
}
export interface LegendItemProps { color: string; text: string; }
export interface PageLayoutProps { title?: string; children: React.ReactNode; className?: string; }

// Updated AnomalyDetailsModalProps
export interface AnomalyDetailsModalProps {
  anomaly: AlertData | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAcknowledge?: (alert: AlertData) => void; // Added optional acknowledge handler
}

// New ReportAnomalyModalProps
export interface ReportAnomalyModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // onSubmit prop expects the data to be potentially POSTed
  onSubmit?: (data: { message: string; severity: SeverityLevel; description?: string; location?: string }) => void;
}

export interface MatrixButtonProps {
 onClick: () => void;
  text: string;
 bgColor?: string;
 textColor?: string;
 backgroundColor?: string;
 children?: React.ReactNode;
}
// --- Backend Data Shape Types ---
export interface BackendCongestionNodeData {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  congestion_score?: number | null; // Optional as per Pydantic model
  vehicle_count?: number | null;   // Optional
  average_speed?: number | null;  // Optional
  timestamp: string; // ISO datetime string
}

export interface AllNodesCongestionResponse {
  nodes: BackendCongestionNodeData[];
}


// --- Hook Return Types ---
// Anomaly Type (used by Anomalies page and components)
export type LocationTuple = [number, number];

export interface Anomaly {
  id: number; // Can be string if WebSocket provides string IDs that can't be parsed to number
  type: string;
  severity: "low" | "medium" | "high"; // Consider if this needs to be broader based on AlertData.severity
  description: string;
  timestamp: string; // ISO string format recommended
  location: LocationTuple;
  resolved: boolean;
  details?: string; // JSON string or specific object structure
  reportedBy?: string;
  source?: 'api' | 'websocket'; // To track origin if merging data
}

export interface RealtimeData {
  isConnected: boolean;
  feeds: FeedStatusData[];
  kpis: KpiData | null;
  alerts: AlertData[];
  nodeCongestionData?: BackendCongestionNodeData[]; // Added for WebSocket node congestion updates
  error: string | null;
}

export interface RealtimeDataActions {
  setInitialFeeds: (feeds: FeedStatusData[]) => void;
  setInitialAlerts: (alerts: AlertData[]) => void;
}

export type UseRealtimeUpdatesReturn = RealtimeData & RealtimeDataActions & { startWebSocket: () => void; sendMessage: (action: string, payload?: object) => boolean; };