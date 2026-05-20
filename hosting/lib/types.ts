// lib/types.ts
import React from 'react';
import { LucideIcon } from 'lucide-react';
import { WebSocketMessageType } from './websocket/WebSocketClient';

// --- Core Data Types ---
export type SeverityLevel = 'Critical' | 'Warning' | 'Anomaly' | 'INFO' | 'ERROR';

export interface FeedStatusData {
  feed_id: string;
  source: string;
  name?: string;
  status: 'stopped' | 'running' | 'starting' | 'error' | 'stopping'; // Added 'stopping'
  fps?: number | null;
  error_message?: string | null;
  latitude?: number; // Added latitude
  longitude?: number; // Added longitude
  config?: {
    name: string;
    source_type: string;
    source_identifier: string;
    latitude: number;
    longitude: number;
    roi?: { x: number; y: number }[];
    exclusion_zones?: { x: number; y: number }[][];
    static_object_filter_enabled?: boolean;
    static_object_timeout?: number;
    show_bounding_boxes?: boolean;
    show_vehicle_details?: boolean;
    show_trajectories?: boolean;
  };
  latest_metrics?: { // Added latest_metrics with a more specific structure
    average_speed_kmh?: number | null;
    total_vehicles?: number | null;
    total_vehicles_cumulative?: number | null;
    session_average_speed_kmh?: number | null;
    lane_occupancy?: Record<string, number>;
    queue_lengths?: Record<string, number>;
    [key: string]: unknown; // Allow for other potential metrics
  } | null;
  latest_vehicles?: any[]; // Array of vehicle data
}

export type IncidentStatus = 'REPORTED' | 'ACKNOWLEDGED' | 'RESOLVED' | 'FALSE_POSITIVE';

export interface AlertData {
  id?: string | number;
  timestamp: string | Date;
  severity: SeverityLevel;
  feed_id?: string | null;
  message: string;
  description?: string;
  latitude?: number; // Added latitude
  longitude?: number; // Added longitude
  location?: string; // This is a simple string; backend might send structured location in details.
  status?: IncidentStatus; // New: stateful status
  assigned_to?: string | null; // New: operator assignment
  resolution_notes?: string | null; // New: notes upon resolution
  updated_at?: string; // New: last change timestamp
  acknowledged?: boolean; // For WebSocket updates on acknowledgement status
  details?: Record<string, unknown>; // To capture full details from WebSocket if needed
}

export interface TrendDataPoint {
  timestamp: string;
  total_vehicles: number;
  avg_speed: number;
  congestion_index: number;
  health_score?: number;
}

export interface KpiData {
  average_speed_kmh?: number | null; // Aligned with backend GlobalRealtimeMetrics, made optional to match backend
  congestion_index?: number | null;  // Aligned with backend GlobalRealtimeMetrics, made optional
  active_incidents_count?: number | null; // Aligned with backend GlobalRealtimeMetrics, made optional
  global_health_score?: number | null; // Aligned with backend GlobalRealtimeMetrics
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
export interface StatCardProps {
  title: string;
  value: string;
  unit?: string; // Added unit property
  icon: React.ElementType;
  statusIcon?: React.ElementType;
  change: string;
  changeText: string;
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

export interface SurveillanceFeedProps {
  feed_id: string; // Changed from feed: FeedStatusData to feed_id: string
  minimalControls?: boolean;
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

// Interface for traffic metrics from a specific feed
export interface TrafficMetrics {
  total_vehicles: number;
  average_speed_kmh: number | null;
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
  signal_id?: string; // Added signal_id
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
  severity: SeverityLevel; // Changed to SeverityLevel
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

export type UseRealtimeUpdatesReturn = RealtimeData & RealtimeDataActions & { sendMessage: (action: WebSocketMessageType, payload?: object) => boolean; };

export interface VehicleFrontendData {
  vehicle_id: string;
  global_vehicle_id?: string;
  bbox: [number, number, number, number];
  speed: number;
  license_plate: string;
  class_id: number;
  class_name: string;
  behavior: string;
  confidence: number;
  is_occluded: boolean;
  status: 'active' | 'tentative' | 'predicting' | 'unknown';
  lane: number;
  vx?: number;
  vy?: number;
  is_wrong_way?: boolean;
  is_stopped?: boolean;
  prev_bbox?: [number, number, number, number];
  last_update_time?: number;
}

export interface SurveillanceFeedMessage {
  total_vehicles?: number;
  average_speed_kmh?: number;
  timestamp?: string | Date;
  latitude?: number;
  longitude?: number;

  // New cumulative/session metrics
  total_vehicles_cumulative?: number;
  session_average_speed_kmh?: number;
  session_congestion_level_percent?: number;

  // Additional instantaneous metrics
  stopped_vehicles?: number;
  congestion_level_percent?: number;
  congestion_index?: number; // Alias or alternative naming
  is_congested?: boolean;
  calibration?: Record<string, { calibrated: boolean, confidence: number, consensus: [number, number] }>;

  // Individual vehicle data is now sent directly in VideoFrameMessage.vehicles
}

export interface VideoFrameMessage {
  feed_id: string;
  frame: string | ArrayBuffer | ImageBitmap;
  frame_index?: number;
  timestamp?: string | number;
  metrics?: SurveillanceFeedMessage;
  vehicles?: VehicleFrontendData[];

}