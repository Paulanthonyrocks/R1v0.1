export enum IncidentStatus {
    NEW = "NEW",
    ACKNOWLEDGED = "ACKNOWLEDGED",
    INVESTIGATING = "INVESTIGATING",
    RESOLVED = "RESOLVED",
    FALSE_ALARM = "FALSE_ALARM"
}

export enum IncidentSeverity {
    LOW = "LOW",
    MEDIUM = "MEDIUM",
    HIGH = "HIGH",
    CRITICAL = "CRITICAL"
}

export enum IncidentType {
    ACCIDENT = "ACCIDENT",
    STALLED_VEHICLE = "STALLED_VEHICLE",
    DEBRIS = "DEBRIS",
    ILLEGAL_PARKING = "ILLEGAL_PARKING",
    WRONG_WAY = "WRONG_WAY",
    PEDESTRIAN_HAZARD = "PEDESTRIAN_HAZARD",
    TRAFFIC_JAM = "TRAFFIC_JAM",
    CONGESTION = "CONGESTION",
    OTHER = "OTHER"
}

export interface Incident {
    id: string;
    feed_id?: string;
    type: IncidentType;
    severity: IncidentSeverity;
    description: string;
    status: IncidentStatus;
    timestamp: number;
    created_at: string;
    updated_at: string;
    latitude?: number;
    longitude?: number;
    snapshot_path?: string;
    assigned_to?: string;
    resolution_notes?: string;
}
