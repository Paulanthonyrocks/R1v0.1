import { useRealtimeState } from '@/lib/context/RealtimeStateContext';
import { WebSocketMessageType } from '@/lib/websocket/WebSocketClient';

export const useRealtimeUpdates = () => {
    const state = useRealtimeState();

    // Send a control message via WS. If the connection isn't authenticated,
    // sendMessage returns false and the message is dropped server-side -- the
    // caller would otherwise see a silent no-op (handleStartAll / handleStopAll
    // being the common case). Surface that to DevTools so the failure mode
    // isn't invisible.
    const sendControl = (action: WebSocketMessageType, payload: object): boolean => {
        const ok = state.sendMessage(action, payload);
        if (!ok) {
            console.warn(
                `[useRealtimeUpdates] ${action} dropped: WebSocket not connected or not authenticated.`
            );
        }
        return ok;
    };

    return {
        ...state,
        // Keep these for compatibility if they are used elsewhere,
        // although they are now part of the state
        startFeed: (feedId: string) => sendControl(WebSocketMessageType.START_FEED, { feed_id: feedId }),
        stopFeed: (feedId: string) => sendControl(WebSocketMessageType.STOP_FEED, { feed_id: feedId }),
        restartFeed: (feedId: string) => {
            sendControl(WebSocketMessageType.STOP_FEED, { feed_id: feedId });
            sendControl(WebSocketMessageType.START_FEED, { feed_id: feedId });
        },
    };
};
