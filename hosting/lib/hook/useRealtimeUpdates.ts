import { useRef, useState, useCallback, useEffect } from 'react';
import { AlertData, FeedStatusData, BackendCongestionNodeData } from '@/lib/types';
import { WebSocketMessageType } from '@/lib/websocket/WebSocketClient';
import { useWebSocket } from '@/lib/websocket/WebSocketProvider';
import {
 _subscribedFeeds,
 resetFeedSubscriptionState
} from '@/lib/websocket/feedSubscriptionState';

interface KPIData {
  timestamp?: string | Date;
  metrics_source?: string;
  congestion_index?: number | null;
  average_speed_kmh?: number | null;
  active_incidents_count?: number | null;
  total_flow?: number | null;
  global_health_score?: number | null;
  feed_statuses?: { [key: string]: number } | null;
  custom_metrics?: { [key: string]: unknown };
  [key: string]: unknown;
}

interface RealtimeUpdates {
  kpis: KPIData | null;
  alerts: AlertData[];
  nodeCongestionData: BackendCongestionNodeData[];
  isConnected: boolean;
  isReady: boolean;
  error: string | null;
  sendMessage: (action: string, payload?: object) => boolean;
  subscribeToFeed: (feedId: string) => void;
  unsubscribeFromFeed: (feedId: string) => void;
}

// ── Module-level dedup for CONTROL messages only ────────────────────────
// Prevents N components from each sending GET_INITIAL_FEED_STATUSES
// and SUBSCRIBE requests on mount. The server-side subscriptions and
// data broadcasts are shared — one request covers all consumers.
let _initialFeedsRequestedForInstance: string | null = null;
let _topicsSubscribedForInstance: string | null = null;
// Track the number of active hook instances so we can detect when all
// consumers unmount (page navigation) and reset the dedup flags.
let _activeHookCount: number = 0;

export const useRealtimeUpdates = (): RealtimeUpdates & {
  feeds: FeedStatusData[];
  startFeed: (feedId: string) => void;
  stopFeed: (feedId: string) => void;
  restartFeed: (feedId: string) => void;
  startWebSocket: () => void;
} => {
  const client = useWebSocket();
  const [kpis, setKpis] = useState<KPIData | null>(null);
  const [alerts, setAlerts] = useState<AlertData[]>([]);
  const [feeds, setFeeds] = useState<FeedStatusData[]>([]);
  const [nodeCongestionData, setNodeCongestionData] = useState<BackendCongestionNodeData[]>([]);
  const [isConnected, setIsConnected] = useState(client.isConnected());
  const [isReady, setIsReady] = useState(client.isConnected());
  const [error, setError] = useState<string | null>(null);

  const sendMessage = useCallback((action: string, payload?: object): boolean => {
    if (client && client.isConnected()) {
      try {
        client.send({ type: action as WebSocketMessageType, data: payload });
        return true;
      } catch (error) {
        console.error('Failed to send message:', error);
        return false;
      }
    }
    console.warn('WebSocket not connected. Message not sent:', { action, payload });
    return false;
  }, [client]);

  const startWebSocket = useCallback(() => {
    if (!client.isConnected()) {
      console.log("startWebSocket called. Client state:", client.getConnectionState());
    }
  }, [client]);

  useEffect(() => {
    const subscriptions: (() => void)[] = [];
    const instanceId = client.getInstanceId();

    // Increment ref count on mount; decrement and reset dedup on unmount
    _activeHookCount++;
    if (_activeHookCount === 1) {
      // First mount on a new page — allow control messages to be sent
      // even if the WebSocket instance ID hasn't changed.
      _initialFeedsRequestedForInstance = null;
      _topicsSubscribedForInstance = null;
    }

    const updateConnectionState = () => {
      const connected = client.isConnected();
      setIsConnected(connected);
      setIsReady(connected);
      return connected;
    };

    const connected = updateConnectionState();

    // ── Deduplicated control messages ────────────────────────────────
    // Only the FIRST hook instance per connection sends these.
    // The server broadcasts to ALL subscribers, so one request covers everyone.

    if (connected && _initialFeedsRequestedForInstance !== instanceId) {
      _initialFeedsRequestedForInstance = instanceId;
      client.send({ type: WebSocketMessageType.GET_INITIAL_FEED_STATUSES, data: {} });
    }

    if (connected && _topicsSubscribedForInstance !== instanceId) {
      _topicsSubscribedForInstance = instanceId;
      client.send({ type: WebSocketMessageType.SUBSCRIBE, data: { topic: 'kpi' } });
      client.send({ type: WebSocketMessageType.SUBSCRIBE, data: { topic: 'node_congestion' } });
    }

    // ── Connection status listener (per-component, lightweight) ──────
    subscriptions.push(client.onStatusChange((status, _message) => {
      const isNowConnected = status === 'connected';
      setIsConnected(isNowConnected);
      setIsReady(isNowConnected);

      if (isNowConnected) {
        if (_initialFeedsRequestedForInstance !== instanceId) {
          _initialFeedsRequestedForInstance = instanceId;
          client.send({ type: WebSocketMessageType.GET_INITIAL_FEED_STATUSES, data: {} });
        }
        if (_topicsSubscribedForInstance !== instanceId) {
          _topicsSubscribedForInstance = instanceId;
          client.send({ type: WebSocketMessageType.SUBSCRIBE, data: { topic: 'kpi' } });
          client.send({ type: WebSocketMessageType.SUBSCRIBE, data: { topic: 'node_congestion' } });
        }
      } else if (status === 'disconnected') {
        _initialFeedsRequestedForInstance = null;
        _topicsSubscribedForInstance = null;
      }
    }));

    subscriptions.push(client.onError((_type, message) => {
      setError(message);
    }));

    // ── Per-component data listeners ─────────────────────────────────
    // Each component registers its own listeners so React state updates
    // flow to the correct component. These are cheap — just function refs
    // in a Set inside WebSocketClient.

    subscriptions.push(client.subscribe(WebSocketMessageType.INITIAL_FEED_STATUSES, (data: { feeds: FeedStatusData[] }) => {
      if (data && Array.isArray(data.feeds)) {
        setFeeds(data.feeds);
      }
    }));

    subscriptions.push(client.subscribe(WebSocketMessageType.FEED_STATUS_UPDATE, (data: { feed_status_data: FeedStatusData }) => {
      if (!data?.feed_status_data) return;
      const statusData = data.feed_status_data;
      setFeeds(prevFeeds => {
        const index = prevFeeds.findIndex(feed => feed.feed_id === statusData.feed_id);
        if (index !== -1) {
          const newFeeds = [...prevFeeds];
          newFeeds[index] = statusData;
          return newFeeds;
        }
        return [...prevFeeds, statusData];
      });
    }));

    subscriptions.push(client.subscribe(WebSocketMessageType.KPI_UPDATE, (data: KPIData) => setKpis(data)));

    subscriptions.push(client.subscribe(WebSocketMessageType.NODE_CONGESTION_UPDATE, (data: { nodes: BackendCongestionNodeData[] }) => {
      if (data && Array.isArray(data.nodes)) {
        setNodeCongestionData(data.nodes);
      }
    }));

    subscriptions.push(client.subscribe(WebSocketMessageType.NEW_ALERT, (data: { alert_data: AlertData }) => {
      if (data?.alert_data) {
        setAlerts(prevAlerts => {
          if (prevAlerts.some(a => a.id === data.alert_data.id)) return prevAlerts;
          return [...prevAlerts, data.alert_data].slice(-20);
        });
      }
    }));

    subscriptions.push(client.subscribe(WebSocketMessageType.GENERAL_NOTIFICATION, (data: any) => {
      if (data?.message_type === 'new_incident') {
        const newIncident: AlertData = {
          id: data.incident_id,
          timestamp: new Date(),
          severity: data.severity,
          feed_id: data.feed_id,
          message: data.title || "New Incident",
          description: data.message,
          status: 'REPORTED'
        };
        setAlerts(prevAlerts => {
          if (prevAlerts.some(a => a.id === newIncident.id)) return prevAlerts;
          return [...prevAlerts, newIncident].slice(-20);
        });
      } else if (data?.message_type === 'incident_update') {
        setAlerts(prevAlerts => prevAlerts.map(alert =>
          alert.id === data.incident_id || String(alert.id) === String(data.incident_id)
            ? { ...alert, status: data.status, resolution_notes: data.notes, updated_at: new Date().toISOString() }
            : alert
        ));
      }
    }));

  return () => {
    subscriptions.forEach(unsubscribe => unsubscribe());
    _activeHookCount--;
    // When all consumers have unmounted (e.g., navigating away from
    // surveillance page), clear the dedup flags so the next page mount
    // will re-request initial statuses and re-subscribe to topics.
    if (_activeHookCount <= 0) {
      _activeHookCount = 0;
      _initialFeedsRequestedForInstance = null;
      _topicsSubscribedForInstance = null;
    }
  };
  }, [client]);

 const subscribeToFeed = useCallback((feedId: string) => {
 // Check if this feed is already subscribed by useVideoSocket
 // If so, don't send another subscribe message
 if (!_subscribedFeeds.has(feedId)) {
 _subscribedFeeds.add(feedId);
 sendMessage(WebSocketMessageType.SUBSCRIBE_TO_FEED, { feed_id: feedId });
 }
 }, [sendMessage]);

 const unsubscribeFromFeed = useCallback((feedId: string) => {
 // Only unsubscribe if this feed is not being managed by useVideoSocket
 if (_subscribedFeeds.has(feedId)) {
 _subscribedFeeds.delete(feedId);
 sendMessage(WebSocketMessageType.UNSUBSCRIBE_FROM_FEED, { feed_id: feedId });
 }
 }, [sendMessage]);

  const startFeed = useCallback((feedId: string) => {
    sendMessage(WebSocketMessageType.START_FEED, { feed_id: feedId });
  }, [sendMessage]);

  const stopFeed = useCallback((feedId: string) => {
    sendMessage(WebSocketMessageType.STOP_FEED, { feed_id: feedId });
  }, [sendMessage]);

  const restartFeed = useCallback((feedId: string) => {
    sendMessage(WebSocketMessageType.RESTART_FEED, { feed_id: feedId });
  }, [sendMessage]);

  return {
    kpis,
    alerts,
    nodeCongestionData,
    isConnected,
    isReady,
    error,
    feeds,
    sendMessage,
    subscribeToFeed,
    unsubscribeFromFeed,
    startFeed,
    stopFeed,
    restartFeed,
    startWebSocket
  };
};