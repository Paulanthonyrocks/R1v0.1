// Corridor view: the cameras watching one road, ordered by distance along
// the road (chainage) so an operator can "drive through" the corridor.
// Pure client-side, from the live feed list + road registry.
import { snapToRoads, angleDeltaDeg, RoadLike } from './geo';
import { FeedStatusData } from '../types';

export interface CorridorCamera {
  feed: FeedStatusData;
  /** Meters from the road's polyline start (chainage). */
  distanceAlongM: number;
  /** Bearing of the road segment the camera snapped to (deg). */
  segmentBearingDeg: number;
  /** Camera heading vs road direction of travel. */
  relation: 'with' | 'against' | 'unknown';
  headingDeg: number | null;
}

/**
 * Feeds assigned to (or snapped near) the road, sorted by chainage ascending.
 * relation: |heading - segment bearing| <= 45 -> 'with', >= 135 -> 'against',
 * otherwise 'unknown' (side-facing); feeds with no heading -> 'unknown'.
 */
export function corridorCameras(
  road: RoadLike,
  feeds: FeedStatusData[],
  opts: { maxSnapDistanceM?: number } = {},
): CorridorCamera[] {
  const maxSnap = opts.maxSnapDistanceM ?? 150;
  const out: CorridorCamera[] = [];
  for (const f of feeds) {
    // Prefer the operator-assigned road; otherwise snap by proximity.
    if (f.config?.road_id && f.config.road_id !== road.id) continue;
    const lat = f.config?.latitude ?? f.latitude;
    const lng = f.config?.longitude ?? f.longitude;
    if (lat == null || lng == null) continue;
    const snap = snapToRoads(lat, lng, [road], maxSnap);
    if (!snap.found) continue;
    const heading = f.config?.heading_deg ?? null;
    let relation: CorridorCamera['relation'] = 'unknown';
    if (heading != null) {
      const delta = Math.abs(angleDeltaDeg(heading, snap.segmentBearingDeg));
      relation = delta <= 45 ? 'with' : delta >= 135 ? 'against' : 'unknown';
    }
    out.push({
      feed: f,
      distanceAlongM: snap.distanceAlongM,
      segmentBearingDeg: snap.segmentBearingDeg,
      relation,
      headingDeg: heading,
    });
  }
  return out.sort((a, b) => a.distanceAlongM - b.distanceAlongM);
}
