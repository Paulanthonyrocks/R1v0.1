import { APIClient } from './api/APIClient';
import { getBackendBaseURL } from './api/backendBaseUrl';

// Client for the safety-hub feature APIs (backend features 1,2,5,7,8,9).
// All fetches tolerate backend-disabled states: callers get null/[],
// never a thrown UI crash.

export type EscalationState = 'OK' | 'DUE' | 'OVERDUE' | 'ACKED';

export interface EscalationInfo {
  incident_id: string;
  state: EscalationState;
  level: string | null;
}

export interface EvidenceManifest {
  incident_id: string;
  type?: string;
  severity?: string;
  feed_id?: string;
  timestamp?: number;
  snapshots: string[];
  clip?: string | null;
  clip_unavailable: boolean;
  bundled_at?: number;
  bundle_dir?: string;
}

export interface WorkZone {
  id: string;
  feed_id?: string;
  lane?: string | number;
  speed_limit?: number;
  starts_at?: number;
  ends_at?: number;
}

export interface PlateCheck {
  plate: string;
  blocked: boolean;
  allowed: boolean;
  known: boolean;
  unconfigured?: boolean;
}

export interface ForensicFilters {
  type?: string;
  severity?: string;
  feed_id?: string;
  lane?: string;
  q?: string;
  limit?: number;
}

// Pure: badge styling for an escalation state. Tested in safetyHub.test.ts.
export function escalationBadgeClass(state: EscalationState | string): string {
  switch (state) {
    case 'OVERDUE':
      return 'bg-red-600/10 text-red-600 border-red-600';
    case 'DUE':
      return 'bg-yellow-500/10 text-yellow-600 border-yellow-600';
    case 'ACKED':
      return 'bg-gray-500/10 text-gray-500 border-gray-500';
    default:
      return 'bg-green-600/10 text-green-700 border-green-700';
  }
}

// Pure: one-line human summary of an evidence manifest. Tested.
export function evidenceSummary(m: EvidenceManifest): string {
  const snaps = m.snapshots?.length ?? 0;
  const clip = m.clip_unavailable ? 'clip unavailable' : `clip ${m.clip ?? 'ready'}`;
  return `${snaps} snapshot${snaps === 1 ? '' : 's'} // ${clip}`;
}

// Pure: drop empty filter values before sending. Tested.
export function cleanFilters(f: ForensicFilters): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(f)) {
    if (v !== undefined && v !== null && String(v) !== '' && String(v) !== 'ALL') {
      out[k] = String(v);
    }
  }
  return out;
}

function client(): APIClient {
  return APIClient.getInstance({ baseURL: getBackendBaseURL() });
}

export async function fetchEscalation(incidentId: string): Promise<EscalationInfo | null> {
  try {
    return await client().get<EscalationInfo>(
      `/api/v1/incidents/${encodeURIComponent(incidentId)}/escalation`
    );
  } catch {
    return null;
  }
}

export async function fetchEvidence(incidentId: string): Promise<EvidenceManifest | null> {
  try {
    return await client().get<EvidenceManifest>(
      `/api/v1/incidents/${encodeURIComponent(incidentId)}/evidence`
    );
  } catch {
    return null;
  }
}

export async function searchIncidents<T = unknown>(filters: ForensicFilters): Promise<T[]> {
  try {
    return await client().get<T[]>('/api/v1/incidents/search/results', cleanFilters(filters));
  } catch {
    return [];
  }
}

export async function fetchWorkZones(feedId?: string): Promise<WorkZone[]> {
  try {
    const params = feedId ? { feed_id: feedId } : undefined;
    const data = await client().get<{ work_zones: WorkZone[] }>(
      '/api/v1/routes/work-zones',
      params
    );
    return data?.work_zones ?? [];
  } catch {
    return [];
  }
}

export async function checkPlate(plate: string): Promise<PlateCheck | null> {
  try {
    return await client().get<PlateCheck>('/api/v1/vehicles/plates/check', { plate });
  } catch {
    return null;
  }
}

export async function fetchPlateLists(): Promise<{ allow: string[]; block: string[] }> {
  try {
    return await client().get<{ allow: string[]; block: string[] }>(
      '/api/v1/vehicles/plates/lists'
    );
  } catch {
    return { allow: [], block: [] };
  }
}
