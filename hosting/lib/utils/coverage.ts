// Coverage-gap analysis: which parts of registered roads no camera cone covers.
// Pure client-side, computed from the live feed list (config.heading_deg /
// fov_deg / lat / lng) and the road registry — same cone geometry as the
// per-feed wedges drawn on the map (LeafletMap feedConesRef).
import { haversineM, bearingDeg, angleDeltaDeg, RoadLike } from './geo';
import { FeedStatusData } from '../types';

export const COVERAGE_RADIUS_M = 150; // matches CONE_RADIUS_M on the map
export const COVERAGE_STEP_M = 10; // sampling interval along each road

export interface GapSegment {
  roadId: string;
  roadName: string;
  from: [number, number];
  to: [number, number];
  lengthM: number;
}

/** True if any headed feed's cone contains the point. */
export function isPointCovered(
  lat: number,
  lng: number,
  feeds: FeedStatusData[],
  radiusM: number = COVERAGE_RADIUS_M,
): boolean {
  for (const f of feeds) {
    const plat = f.config?.latitude ?? f.latitude;
    const plng = f.config?.longitude ?? f.longitude;
    const h = f.config?.heading_deg;
    if (plat == null || plng == null || h == null) continue;
    if (haversineM(plat, plng, lat, lng) > radiusM) continue;
    const fov = f.config?.fov_deg ?? 60;
    const toPoint = bearingDeg(plat, plng, lat, lng);
    if (Math.abs(angleDeltaDeg(h, toPoint)) <= fov / 2) return true;
  }
  return false;
}

/** Interpolate a point `alongM` meters along a polyline of [lat, lng]. */
export function pointAlongPolyline(
  polyline: [number, number][],
  alongM: number,
): [number, number] | null {
  if (polyline.length < 2) return null;
  let remaining = alongM;
  for (let i = 0; i < polyline.length - 1; i++) {
    const a = polyline[i];
    const b = polyline[i + 1];
    const segLen = haversineM(a[0], a[1], b[0], b[1]);
    if (segLen < 1e-9) continue;
    if (remaining <= segLen) {
      const t = remaining / segLen;
      return [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
    }
    remaining -= segLen;
  }
  return null; // beyond the end
}

/**
 * Uncovered runs along one road. Samples every stepM; each maximal run of
 * uncovered samples becomes one GapSegment spanning from the last covered
 * sample (or road start) to the next covered sample (or road end).
 */
export function roadCoverageGaps(
  road: RoadLike,
  feeds: FeedStatusData[],
  opts: { radiusM?: number; stepM?: number } = {},
): GapSegment[] {
  const radiusM = opts.radiusM ?? COVERAGE_RADIUS_M;
  const stepM = opts.stepM ?? COVERAGE_STEP_M;
  const poly = road.polyline.map(p => [p.lat, p.lng] as [number, number]);
  if (poly.length < 2) return [];
  let total = 0;
  for (let i = 0; i < poly.length - 1; i++) total += haversineM(poly[i][0], poly[i][1], poly[i + 1][0], poly[i + 1][1]);

  const gaps: GapSegment[] = [];
  let gapStart: [number, number] | null = null;
  let gapLen = 0;
  let prevPoint: [number, number] = poly[0];
  const flush = (end: [number, number]) => {
    if (gapStart) {
      gaps.push({
        roadId: road.id,
        roadName: road.name,
        from: gapStart,
        to: end,
        lengthM: gapLen + stepM,
      });
      gapStart = null;
      gapLen = 0;
    }
  };
  for (let d = stepM; d <= total + 1e-6; d += stepM) {
    const pt = pointAlongPolyline(poly, Math.min(d, total));
    if (!pt) break;
    const covered = isPointCovered(pt[0], pt[1], feeds, radiusM);
    if (!covered) {
      if (!gapStart) gapStart = prevPoint;
      gapLen += stepM;
    } else {
      flush(pt);
    }
    prevPoint = pt;
  }
  flush(poly[poly.length - 1]);
  return gaps;
}

/** All gaps across all roads. */
export function allCoverageGaps(
  roads: RoadLike[],
  feeds: FeedStatusData[],
  opts: { radiusM?: number; stepM?: number } = {},
): GapSegment[] {
  return roads.flatMap(r => roadCoverageGaps(r, feeds, opts));
}
