import { useRealtimeState } from '@/lib/context/RealtimeStateContext';

export const useRealtimeUpdates = () => {
    const state = useRealtimeState();
    
    return {
        ...state,
        // Keep these for compatibility if they are used elsewhere, 
        // although they are now part of the state
        startFeed: (feedId: string) => state.sendMessage('START_FEED', { feed_id: feedId }),
        stopFeed: (feedId: string) => state.sendMessage('STOP_FEED', { feed_id: feedId }),
        restartFeed: (feedId: string) => {
            state.sendMessage('STOP_FEED', { feed_id: feedId });
            state.sendMessage('START_FEED', { feed_id: feedId });
        },
    };
};
