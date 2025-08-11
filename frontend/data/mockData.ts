// data/mockData.ts
import { StatCardData, AlertData, CongestionNodeProps, FeedStatusData } from '@/lib/types';
import { Car, Bug, Gauge, Network } from 'lucide-react'; // Assuming MapPin was just for placeholder example

export const mockStatCards: StatCardData[] = [
    { id: 'stat1', title: "Total Flow", value: "1,248", change: "+12%", changeText: "vs yesterday", icon: Car, valueColor: "text-primary" },
    { id: 'stat2', title: "Anomalies", value: "14", change: "+3", changeText: "new today", icon: Bug, valueColor: "text-amber-400" },
    { id: 'stat3', title: "Avg. Speed", value: "32 mph", change: "-8%", changeText: "vs yesterday", icon: Gauge, valueColor: "text-green-400", changeColor: "text-amber-400" },
    { id: 'stat4', title: "Node Efficiency", value: "87%", change: "+5%", changeText: "improvement", icon: Network, valueColor: "text-green-500" },
];

export const mockAnomalyItems: AlertData[] = [
    { id: 'anom1', message: "Collision Detected", description: "2 vehicles involved", location: "Main St & 5th Ave", timestamp: "15 min ago", severity: "Critical" },
    { id: 'anom2', message: "Node Maintenance", description: "Lane closure", location: "Broadway & 12th St", timestamp: "1 hour ago", severity: "Warning" },
    { id: 'anom3', message: "Data Spike", description: "Unusual sensor reading", location: "Node #A7", timestamp: "30 min ago", severity: "Anomaly" },
    { id: 'anom4', message: "Disabled Vehicle", description: "Right lane blocked", location: "I-95 Exit 14", timestamp: "2 hours ago", severity: "Warning" },
    { id: 'anom5', message: "Signal Failure", description: "Node offline", location: "Park Ave & 34th St", timestamp: "3 hours ago", severity: "Critical" },
    // Added item for scroll testing
    { id: 'anom6', message: "Heavy Congestion", description: "Standstill traffic reported", location: "Highway 101 S Exit 4B", timestamp: "5 min ago", severity: "Warning" },
];

export const mockCongestionNodes: CongestionNodeProps[] = [
    { id: 'cong1', name: "Main St & 5th Ave", value: 87 },
    { id: 'cong2', name: "Broadway & 12th St", value: 72 },
    { id: 'cong3', name: "I-95 Exit 14", value: 65 },
    { id: 'cong4', name: "Park Ave & 34th St", value: 58 },
];

export const mockSurveillanceFeeds: FeedStatusData[] = [
    { id: 'feed1', name: "Main St & 5th Ave", source: "#TC-142", status: 'running' },
    { id: 'feed2', name: "Broadway & 12th St", source: "#TC-187", status: 'running' },
    { id: 'feed3', name: "I-95 Exit 14", source: "#TC-205", status: 'running' },
    { id: 'feed4', name: "Park Ave & 34th St", source: "#TC-091", status: 'running' },
];