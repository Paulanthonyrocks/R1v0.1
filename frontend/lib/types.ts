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
  location?: string;
}

export interface TrendDataPoint {
  timestamp: string;
  total_vehicles: number;
  avg_speed: number;
  congestion_index?: number;
}

export interface KpiData {
  avg_speed: number;
  congestion_index: number;
  active_incidents: number;
  feed_status_counts: { running: number; error: number; idle: number };
  total_flow?: number;
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
// --- Hook Return Types ---
export interface RealtimeData {
  isConnected: boolean;
  feeds: FeedStatusData[];
  kpis: KpiData | null;
  alerts: AlertData[];
  error: string | null;
}

export interface RealtimeDataActions {
  setInitialFeeds: (feeds: FeedStatusData[]) => void;
  setInitialAlerts: (alerts: AlertData[]) => void;
}

export type UseRealtimeUpdatesReturn = RealtimeData & RealtimeDataActions & { startWebSocket: () => void; sendMessage: (action: string, payload?: object) => boolean; };