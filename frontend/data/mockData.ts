// data/mockData.ts
import { StatCardData, AnomalyItemData, CongestionNodeData, SurveillanceFeedData } from '@/lib/types';
import { Car, Bug, Gauge, Network } from 'lucide-react'; // Assuming MapPin was just for placeholder example

export const mockStatCards: StatCardData[] = [
    { id: 'stat1', title: "Total Flow", value: "1,248", change: "+12%", changeText: "vs yesterday", icon: Car, valueColor: "text-primary" },
    { id: 'stat2', title: "Anomalies", value: "14", change: "+3", changeText: "new today", icon: Bug, valueColor: "text-amber-400" },
    { id: 'stat3', title: "Avg. Speed", value: "32 mph", change: "-8%", changeText: "vs yesterday", icon: Gauge, valueColor: "text-green-400", changeColor: "text-amber-400" },
    { id: 'stat4', title: "Node Efficiency", value: "87%", change: "+5%", changeText: "improvement", icon: Network, valueColor: "text-green-500" },
];

export const mockAnomalyItems: AnomalyItemData[] = [
    { id: 'anom1', title: "Collision Detected", description: "2 vehicles involved", location: "Main St & 5th Ave", time: "15 min ago", severity: "Critical" },
    { id: 'anom2', title: "Node Maintenance", description: "Lane closure", location: "Broadway & 12th St", time: "1 hour ago", severity: "Warning" },
    { id: 'anom3', title: "Data Spike", description: "Unusual sensor reading", location: "Node #A7", time: "30 min ago", severity: "Anomaly" },
    { id: 'anom4', title: "Disabled Vehicle", description: "Right lane blocked", location: "I-95 Exit 14", time: "2 hours ago", severity: "Warning" },
    { id: 'anom5', title: "Signal Failure", description: "Node offline", location: "Park Ave & 34th St", time: "3 hours ago", severity: "Critical" },
    // Added item for scroll testing
    { id: 'anom6', title: "Heavy Congestion", description: "Standstill traffic reported", location: "Highway 101 S Exit 4B", time: "5 min ago", severity: "Warning" },
];

export const mockCongestionNodes: CongestionNodeData[] = [
    { id: 'cong1', name: "Main St & 5th Ave", value: 87 },
    { id: 'cong2', name: "Broadway & 12th St", value: 72 },
    { id: 'cong3', name: "I-95 Exit 14", value: 65 },
    { id: 'cong4', name: "Park Ave & 34th St", value: 58 },
];

export const mockSurveillanceFeeds: SurveillanceFeedData[] = [
    { id: 'feed1', name: "Main St & 5th Ave", node: "#TC-142" },
    { id: 'feed2', name: "Broadway & 12th St", node: "#TC-187" },
    { id: 'feed3', name: "I-95 Exit 14", node: "#TC-205" },
    { id: 'feed4', name: "Park Ave & 34th St", node: "#TC-091" },
];