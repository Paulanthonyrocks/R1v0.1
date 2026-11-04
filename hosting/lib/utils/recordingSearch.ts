// Placement-aware filtering for the recordings archive and route history, so
// they speak the same road:/dir: language as the live map search
// (lib/utils/feedSearch.ts).
import { FeedStatusData } from '../types';
import { RoadLike } from './geo';
import { ParsedFeedQuery, matchesFeed, matchesNodeQuery } from './feedSearch';
import { NodeCongestionData } from '../types/api';

export interface StreamTagged {
    stream_id: string;
}

/**
 * A recording belongs to the query when its feed (stream_id) matches the
 * placement query — road:, dir:, status:, or free text on the feed.
 * Unplaced feeds only match free text / no query.
 */
export function streamMatchesQuery(
    item: StreamTagged,
    q: ParsedFeedQuery,
    feeds: FeedStatusData[],
    roads: RoadLike[],
): boolean {
    if (!q.text && !q.road && !q.dir && !q.status) return true;
    const feed = feeds.find(f => f.feed_id === item.stream_id);
    if (!feed) return false;
    return matchesFeed(feed, q, roads);
}

/** Road/facing badge text for a stream's feed, e.g. "Main St · E 90°". */
export function streamPlacementBadge(
    item: StreamTagged,
    feeds: FeedStatusData[],
    roads: RoadLike[],
): string | null {
    const feed = feeds.find(f => f.feed_id === item.stream_id);
    if (!feed || !feed.config?.road_id) return null;
    const road = roads.find(r => r.id === feed.config!.road_id);
    const roadLabel = road?.name ?? feed.config.road_id;
    const h = feed.config.heading_deg;
    const compass = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
    const dirLabel = h != null ? `${compass[Math.round((((h % 360) + 360) % 360) / 22.5) % 16]} ${Math.round(h)}°` : '';
    return [roadLabel, dirLabel].filter(Boolean).join(' · ');
}

/** Route-history rows: keep routes whose start OR end node matches the query
 * (nodes are "lat,lng" ids; road: matches roads listing them as intersections). */
export function routeMatchesQuery(
    route: { start: string; end: string },
    q: ParsedFeedQuery,
    roads: RoadLike[],
): boolean {
    if (!q.text && !q.road) return true;
    const asNode = (id: string): NodeCongestionData => ({ id, name: '', vehicle_count: 0, avg_speed_kmh: 0, congestion_level: 'low' } as unknown as NodeCongestionData);
    return matchesNodeQuery(asNode(route.start), q, roads) || matchesNodeQuery(asNode(route.end), q, roads);
}
