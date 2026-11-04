// Pure geo helpers for map overlays (camera heading cones). No deps.

const EARTH_RADIUS_M = 6371008.8;
const TO_RAD = Math.PI / 180;
const TO_DEG = 180 / Math.PI;

/** Destination point from (lat, lng) traveling distM along initial bearing (deg, 0=N). */
export function destinationPoint(
  lat: number,
  lng: number,
  bearingDeg: number,
  distM: number,
): [number, number] {
  const d = distM / EARTH_RADIUS_M;
  const th = bearingDeg * TO_RAD;
  const lat1 = lat * TO_RAD;
  const lng1 = lng * TO_RAD;
  const lat2 = Math.asin(
    Math.sin(lat1) * Math.cos(d) + Math.cos(lat1) * Math.sin(d) * Math.cos(th),
  );
  const lng2 =
    lng1 +
    Math.atan2(
      Math.sin(th) * Math.sin(d) * Math.cos(lat1),
      Math.cos(d) - Math.sin(lat1) * Math.sin(lat2),
    );
  return [lat2 * TO_DEG, ((lng2 * TO_DEG + 540) % 360) - 180];
}

/**
 * Polygon points for a camera coverage wedge: apex at (lat, lng), facing
 * headingDeg, half-angle fovDeg/2, radius radiusM. Returns [lat, lng][] ready
 * for L.polygon. Leaflet heading 0 = north, 90 = east (same convention as the
 * backend's bearing_deg).
 */
export function headingConeLatLngs(
  lat: number,
  lng: number,
  headingDeg: number,
  fovDeg: number,
  radiusM: number,
  segments = 24,
): [number, number][] {
  const pts: [number, number][] = [[lat, lng]];
  const half = Math.min(Math.abs(fovDeg), 360) / 2;
  for (let i = 0; i <= segments; i++) {
    const b = headingDeg - half + (2 * half * i) / segments;
    pts.push(destinationPoint(lat, lng, ((b % 360) + 360) % 360, radiusM));
  }
  return pts;
}

/** Shortest signed angle from a to b in degrees, in (-180, 180]. */
export function angleDeltaDeg(a: number, b: number): number {
  let d = (b - a) % 360;
  if (d > 180) d -= 360;
  if (d <= -180) d += 360;
  return d;
}

/** Initial compass bearing from point 1 to point 2, degrees [0, 360). */
export function bearingDeg(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const p1 = lat1 * TO_RAD;
  const p2 = lat2 * TO_RAD;
  const dl = (lng2 - lng1) * TO_RAD;
  const x = Math.sin(dl) * Math.cos(p2);
  const y = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return ((Math.atan2(x, y) * TO_DEG) + 360) % 360;
}

export function haversineM(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const p1 = lat1 * TO_RAD;
  const p2 = lat2 * TO_RAD;
  const dp = p2 - p1;
  const dl = (lng2 - lng1) * TO_RAD;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** Equirectangular meters of (lat, lng) relative to (lat0, lng0). */
function toXY(lat: number, lng: number, lat0: number, lng0: number): [number, number] {
  return [
    (lng - lng0) * TO_RAD * Math.cos(lat0 * TO_RAD) * EARTH_RADIUS_M,
    (lat - lat0) * TO_RAD * EARTH_RADIUS_M,
  ];
}

function toLL(x: number, y: number, lat0: number, lng0: number): [number, number] {
  return [
    lat0 + (y / EARTH_RADIUS_M) * TO_DEG,
    lng0 + (x / (EARTH_RADIUS_M * Math.cos(lat0 * TO_RAD))) * TO_DEG,
  ];
}

export interface PolylineProjection {
  segmentIndex: number;
  t: number;
  point: [number, number];
  distanceM: number;
  /** Arc length from the polyline start to the projected point (m). */
  distanceAlongM: number;
  /** Compass heading of the segment the point landed on (deg). */
  segmentBearingDeg: number;
}

/** Closest point on a polyline of [lat, lng] to (lat, lng). Mirrors backend geo_utils. */
export function projectPointToPolyline(
  lat: number,
  lng: number,
  polyline: [number, number][],
): PolylineProjection | null {
  if (!polyline || polyline.length < 2) return null;
  let best: { dist: number; i: number; t: number; proj: [number, number]; alongBefore: number } | null = null;
  let along = 0;
  for (let i = 0; i < polyline.length - 1; i++) {
    const a = polyline[i];
    const b = polyline[i + 1];
    const segLen = haversineM(a[0], a[1], b[0], b[1]);
    let proj: [number, number];
    let t: number;
    let dist: number;
    if (segLen < 1e-9) {
      proj = a;
      t = 0;
      dist = haversineM(lat, lng, a[0], a[1]);
    } else {
      const [ax, ay] = toXY(a[0], a[1], lat, lng);
      const [bx, by] = toXY(b[0], b[1], lat, lng);
      const dx = bx - ax;
      const dy = by - ay;
      t = Math.max(0, Math.min(1, ((-ax * dx) + (-ay * dy)) / (segLen * segLen)));
      const px = ax + t * dx;
      const py = ay + t * dy;
      proj = toLL(px, py, lat, lng);
      dist = Math.hypot(px, py);
    }
    if (!best || dist < best.dist) best = { dist, i, t, proj, alongBefore: along };
    along += segLen;
  }
  if (!best) return null;
  const a = polyline[best.i];
  const b = polyline[best.i + 1];
  const segLen = haversineM(a[0], a[1], b[0], b[1]);
  return {
    segmentIndex: best.i,
    t: best.t,
    point: best.proj,
    distanceM: best.dist,
    distanceAlongM: best.alongBefore + best.t * segLen,
    segmentBearingDeg: bearingDeg(a[0], a[1], b[0], b[1]),
  };
}

export interface RoadLike {
  id: string;
  name: string;
  polyline: { lat: number; lng: number }[];
  intersection_ids?: string[];
}

export type RoadSnapResult =
  | { found: true; road: RoadLike; distanceM: number; distanceAlongM: number; segmentBearingDeg: number; point: [number, number] }
  | { found: false };

/** Nearest road to a point within maxDistanceM. Mirrors backend snap_to_roads. */
export function snapToRoads(
  lat: number,
  lng: number,
  roads: RoadLike[],
  maxDistanceM: number,
): RoadSnapResult {
  let best: { dist: number; road: RoadLike; proj: PolylineProjection } | null = null;
  for (const road of roads) {
    const poly = road.polyline.map(p => [p.lat, p.lng] as [number, number]);
    const proj = projectPointToPolyline(lat, lng, poly);
    if (proj && (!best || proj.distanceM < best.dist)) best = { dist: proj.distanceM, road, proj };
  }
  if (!best || best.dist > maxDistanceM) return { found: false };
  return {
    found: true,
    road: best.road,
    distanceM: best.dist,
    distanceAlongM: best.proj.distanceAlongM,
    segmentBearingDeg: best.proj.segmentBearingDeg,
    point: best.proj.point,
  };
}
