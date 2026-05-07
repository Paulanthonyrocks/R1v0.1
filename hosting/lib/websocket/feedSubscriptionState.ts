/**
 * Module-level dedup state for feed subscriptions.
 *
 * Both `useRealtimeUpdates` and `useVideoSocket` send SUBSCRIBE_TO_FEED /
 * UNSUBSCRIBE_FROM_FEED messages. Since the server-side subscription is
 * per-client (not per-component), one subscribe covers all consumers.
 *
 * This shared module ensures both hooks see the same subscription state
 * and don't double-subscribe the same feed.
 */

// Feeds currently subscribed on the server (per-client, shared across all hooks)
export const _subscribedFeeds: Set<string> = new Set();

// Track active hook instances per feed for ref-counting
export const _feedHookCounts: Map<string, number> = new Map();

// Pending unsubscribe timers — debounced so React Strict Mode remounts
// don't cause a rapid unsubscribe->subscribe flicker
export const _pendingUnsubscribes: Map<string, ReturnType<typeof setTimeout>> = new Map();

// Pending worker cleanup timers — debounced alongside unsubscribe
export const _pendingCleanups: Map<string, ReturnType<typeof setTimeout>> = new Map();

export const UNSUBSCRIBE_DEBOUNCE_MS = 1000;

/** Clear all subscription tracking state (call on hard disconnect only) */
export function resetFeedSubscriptionState(): void {
  _subscribedFeeds.clear();
  for (const [, timer] of _pendingUnsubscribes) clearTimeout(timer);
  _pendingUnsubscribes.clear();
  for (const [, timer] of _pendingCleanups) clearTimeout(timer);
  _pendingCleanups.clear();
}