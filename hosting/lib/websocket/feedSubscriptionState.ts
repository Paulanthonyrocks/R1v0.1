import { WebSocketMessageType, type WebSocketMessage } from './WebSocketClient';

interface FeedClient {
  send(message: WebSocketMessage): void;
  getConnectionState(): string;
  onStatusChange(listener: (status: string) => void): () => void;
  cleanupWorkerResources(feedId: string): void;
}
interface FeedEntry {
  owners: number;
  subscribedEpoch: number;
  timer?: ReturnType<typeof setTimeout>;
}
interface ClientState {
  feeds: Map<string, FeedEntry>;
  epoch: number;
  authenticated: boolean;
  unsubscribeStatus: () => void;
}
const clients = new WeakMap<FeedClient, ClientState>();
export const UNSUBSCRIBE_DEBOUNCE_MS = 1000;

/** Retain demand, not wire state. One subscription per client/feed/auth epoch. */
export function retainFeedSubscription(client: FeedClient, feedId: string): () => void {
  if (!feedId) return () => {};
  let state = clients.get(client);
  if (!state) {
    state = { feeds: new Map(), epoch: 0, authenticated: false, unsubscribeStatus: () => {} };
    clients.set(client, state);
    const current = state;
    current.unsubscribeStatus = client.onStatusChange(status => {
      if (status !== 'authenticated') { current.authenticated = false; return; }
      if (!current.authenticated) current.epoch++;
      current.authenticated = true;
      for (const [id, entry] of current.feeds) subscribe(client, current, id, entry);
    });
  }
  let entry = state.feeds.get(feedId);
  if (!entry) {
    entry = { owners: 0, subscribedEpoch: -1 };
    state.feeds.set(feedId, entry);
  }
  if (entry.timer) clearTimeout(entry.timer);
  entry.timer = undefined;
  entry.owners++;
  subscribe(client, state, feedId, entry);
  let released = false;
  return () => {
    if (released || clients.get(client) !== state) return;
    released = true;
    if (--entry.owners > 0) return;
    entry.timer = setTimeout(() => {
      entry.timer = undefined;
      if (entry.owners > 0 || clients.get(client) !== state) return;
      if (state.authenticated && entry.subscribedEpoch === state.epoch) {
        client.send({ type: WebSocketMessageType.UNSUBSCRIBE_FROM_FEED, data: { feed_id: feedId } });
      }
      client.cleanupWorkerResources(feedId);
      state.feeds.delete(feedId);
      if (!state.feeds.size) {
        state.unsubscribeStatus();
        clients.delete(client);
      }
    }, UNSUBSCRIBE_DEBOUNCE_MS);
  };
}

function subscribe(client: FeedClient, state: ClientState, feedId: string, entry: FeedEntry): void {
  if (!entry.owners || !state.authenticated || entry.subscribedEpoch === state.epoch) return;
  entry.subscribedEpoch = state.epoch;
  client.send({ type: WebSocketMessageType.SUBSCRIBE_TO_FEED, data: { feed_id: feedId } });
}

/** Destroy drops demand and cancels timers; ordinary outages retain owners. */
export function resetFeedSubscriptionState(client: FeedClient): void {
  const state = clients.get(client);
  if (!state) return;
  state.unsubscribeStatus();
  for (const entry of state.feeds.values()) if (entry.timer) clearTimeout(entry.timer);
  clients.delete(client);
}
