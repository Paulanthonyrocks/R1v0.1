// Placement-aware feed/node search query language, shared by the map search
// (LeafletMap.handleSearch) and the surveillance page filter.
//
// Syntax: free text plus key:value tokens (values may be "quoted with spaces"):
//   road:"Main St"     feed assigned to that road (id or name substring)
//   dir:NE | dir:90    camera facing within +/-22.5 deg of that compass/heading
//   dir:with-road      feed heading agrees with its road's direction of travel (+/-45)
//   dir:against-road   feed heading opposes its road's direction of travel
//   status:running     feed status (running|stopped|error|starting|stopping)
//   free text          matches feed name/feed_id/source, or node id/name
// Nodes also match road: when the node id is listed in the road's
// intersection_ids.
import { angleDeltaDeg, snapToRoads, RoadLike } from './geo';
import { FeedStatusData } from '../types';
import { NodeCongestionData } from '../types/api';

export interface ParsedFeedQuery {
  text: string;
  road?: string;
  dir?: string;
  status?: string;
}

/** 16-point compass, degrees clockwise from north. */
export const COMPASS: Record<string, number> = {
  N: 0, NNE: 22.5, NE: 45, ENE: 67.5, E: 90, ESE: 112.5, SE: 135, SSE: 157.5,
  S: 180, SSW: 202.5, SW: 225, WSW: 247.5, W: 270, WNW: 292.5, NW: 315, NNW: 337.5,
};

export function parseFeedQuery(query: string): ParsedFeedQuery {
  const out: ParsedFeedQuery = { text: '' };
  // key:"quoted value" must tokenize as one unit, before bare \S+.
  const tokens = query.match(/\w+:"[^"]*"|"[^"]*"|\S+/g) || [];
  const free: string[] = [];
  for (const tok of tokens) {
    const m = tok.match(/^(road|dir|status):(.+)$/i);
    if (m) {
      const key = m[1].toLowerCase();
      let val = m[2];
      if (val.startsWith('"') && val.endsWith('"') && val.length >= 2) val = val.slice(1, -1);
      if (key === 'road') out.road = val.toLowerCase();
      else if (key === 'dir') out.dir = val.toLowerCase();
      else if (key === 'status') out.status = val.toLowerCase();
    } else {
      free.push(tok.replace(/^"|"$/g, ''));
    }
  }
  out.text = free.join(' ').trim().toLowerCase();
  return out;
}

/** Resolve a road filter against the registry: exact id, then name substring. */
export function resolveRoad(q: string | undefined, roads: RoadLike[]): RoadLike | null {
  if (!q) return null;
  return (
    roads.find(r => r.id.toLowerCase() === q) ||
    roads.find(r => r.name.toLowerCase().includes(q)) ||
    null
  );
}

function dirTargetDeg(dir: string): number | null {
  const up = dir.toUpperCase();
  if (up in COMPASS) return COMPASS[up];
  const n = Number(dir);
  return Number.isFinite(n) ? ((n % 360) + 360) % 360 : null;
}

/** Absolute direction match: |heading - target| <= 22.5 deg (one compass sector). */
export function headingMatchesDir(headingDeg: number, dir: string): boolean {
  const target = dirTargetDeg(dir);
  if (target == null) return false;
  return Math.abs(angleDeltaDeg(headingDeg, target)) <= 22.5;
}

/**
 * Direction relative to the feed's road: snap the camera position to its
 * assigned road, take that segment's bearing, and compare with the camera
 * heading. with-road: within +/-45 deg; against-road: beyond 135 deg.
 */
export function headingMatchesRoadDir(
  headingDeg: number,
  feed: FeedStatusData,
  dir: 'with-road' | 'against-road',
  roads: RoadLike[],
): boolean {
  const roadId = feed.config?.road_id;
  const lat = feed.config?.latitude ?? feed.latitude;
  const lng = feed.config?.longitude ?? feed.longitude;
  if (!roadId || lat == null || lng == null) return false;
  const road = roads.find(r => r.id === roadId);
  if (!road) return false;
  const snap = snapToRoads(lat, lng, [road], 1e9);
  if (!snap.found) return false;
  const delta = Math.abs(angleDeltaDeg(headingDeg, snap.segmentBearingDeg));
  return dir === 'with-road' ? delta <= 45 : delta >= 135;
}

export function matchesFeed(feed: FeedStatusData, q: ParsedFeedQuery, roads: RoadLike[]): boolean {
  if (q.status && feed.status !== q.status) return false;
  if (q.road) {
    const rid = feed.config?.road_id;
    if (!rid) return false;
    const road = resolveRoad(q.road, roads);
    // Known road: must be the one assigned; unknown text: substring fallback.
    if (road ? road.id !== rid : !rid.toLowerCase().includes(q.road)) return false;
  }
  if (q.dir) {
    const h = feed.config?.heading_deg;
    if (h == null) return false;
    if (q.dir === 'with-road' || q.dir === 'against-road') {
      if (!headingMatchesRoadDir(h, feed, q.dir, roads)) return false;
    } else if (!headingMatchesDir(h, q.dir)) {
      return false;
    }
  }
  if (q.text) {
    const hay = `${feed.name || ''} ${feed.feed_id} ${feed.source}`.toLowerCase();
    if (!hay.includes(q.text)) return false;
  }
  return true;
}

export function matchesNodeQuery(node: NodeCongestionData, q: ParsedFeedQuery, roads: RoadLike[]): boolean {
  if (q.road) {
    const road = resolveRoad(q.road, roads);
    const inRoad = road?.intersection_ids?.includes(node.id) ?? false;
    // If the road filter names a known road, the node must be one of its
    // intersections; if it matches nothing, fall through to text-only.
    if (road && !inRoad) return false;
  }
  if (q.text) {
    const hay = `${node.id} ${node.name || ''}`.toLowerCase();
    if (!hay.includes(q.text)) return false;
  }
  return true;
}
