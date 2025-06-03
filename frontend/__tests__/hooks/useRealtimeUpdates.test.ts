import { renderHook, act } from '@testing-library/react-hooks';
import { useRealtimeUpdates } from '@/lib/hook'; // Assuming hook.ts is where useRealtimeUpdates is
import { AlertData, BackendCongestionNodeData } from '@/lib/types';

// Define WebSocketMessageTypeEnum values used by the hook, mirroring backend
const WebSocketMessageTypeEnum = {
  ALERT_STATUS_UPDATE: "alert_status_update",
  NODE_CONGESTION_UPDATE: "node_congestion_update",
  // Add other types if the hook processes them and they need testing
};

// Mock the WebSocket global object
let mockServer: {
  onmessage: ((event: MessageEvent) => void) | null;
  send: jest.Mock;
  close: jest.Mock;
  readyState: number;
};

beforeEach(() => {
  mockServer = {
    onmessage: null,
    send: jest.fn(),
    close: jest.fn(),
    readyState: WebSocket.OPEN, // Simulate open initially for some tests
  };

  // @ts-ignore
  global.WebSocket = jest.fn(() => ({
    onopen: jest.fn(),
    onclose: jest.fn(),
    onerror: jest.fn(),
    onmessage: (callback: (event: MessageEvent) => void) => {
      mockServer.onmessage = callback; // Allow tests to trigger onmessage
    },
    send: mockServer.send,
    close: mockServer.close,
    readyState: WebSocket.OPEN, // Default to OPEN
    removeEventListener: jest.fn(),
    addEventListener: jest.fn(),
    dispatchEvent: jest.fn(),
  })) as any;

  // Clear any timers from previous tests
  jest.clearAllTimers();
});

afterEach(() => {
  // @ts-ignore
  delete global.WebSocket;
  jest.restoreAllMocks();
});


describe('useRealtimeUpdates Hook', () => {
  it('should initialize with default values', () => {
    const { result } = renderHook(() => useRealtimeUpdates());
    expect(result.current.isConnected).toBe(false);
    expect(result.current.isReady).toBe(false);
    expect(result.current.alerts).toEqual([]);
    expect(result.current.nodeCongestionData).toEqual([]); // Assuming it's initialized to []
    expect(result.current.kpis).toBeNull();
    expect(result.current.error).toBeNull();
  });

  describe('WebSocket Message Handling', () => {
    it('should handle ALERT_STATUS_UPDATE: dismissed', () => {
      const initialAlerts: AlertData[] = [
        { id: '1', message: 'Alert 1', timestamp: new Date().toISOString(), severity: 'high', acknowledged: false },
        { id: '2', message: 'Alert 2', timestamp: new Date().toISOString(), severity: 'medium', acknowledged: false },
      ];
      const { result } = renderHook(() => useRealtimeUpdates());

      act(() => {
        result.current.setInitialAlerts(initialAlerts); // Populate initial alerts
      });
      expect(result.current.alerts).toHaveLength(2);

      const updatePayload = {
        alert_id: '1',
        status: 'dismissed',
        timestamp: new Date().toISOString(),
      };

      act(() => {
        // Simulate receiving a message
        if (mockServer.onmessage) {
          mockServer.onmessage(new MessageEvent('message', {
            data: JSON.stringify({ type: WebSocketMessageTypeEnum.ALERT_STATUS_UPDATE, payload: updatePayload }),
          }));
        }
      });

      expect(result.current.alerts).toHaveLength(1);
      expect(result.current.alerts.find(a => a.id === '1')).toBeUndefined();
      expect(result.current.alerts.find(a => a.id === '2')).toBeDefined();
    });

    it('should handle ALERT_STATUS_UPDATE: acknowledged', () => {
      const initialAlerts: AlertData[] = [
        { id: '1', message: 'Alert 1', timestamp: new Date().toISOString(), severity: 'high', acknowledged: false },
      ];
      const { result } = renderHook(() => useRealtimeUpdates());
      act(() => result.current.setInitialAlerts(initialAlerts));

      const updatePayload = { alert_id: '1', status: 'acknowledged', timestamp: new Date().toISOString() };
      act(() => {
        if (mockServer.onmessage) {
          mockServer.onmessage(new MessageEvent('message', {
            data: JSON.stringify({ type: WebSocketMessageTypeEnum.ALERT_STATUS_UPDATE, payload: updatePayload }),
          }));
        }
      });

      expect(result.current.alerts[0].acknowledged).toBe(true);
    });

    it('should handle ALERT_STATUS_UPDATE: unacknowledged', () => {
      const initialAlerts: AlertData[] = [
        { id: '1', message: 'Alert 1', timestamp: new Date().toISOString(), severity: 'high', acknowledged: true },
      ];
      const { result } = renderHook(() => useRealtimeUpdates());
      act(() => result.current.setInitialAlerts(initialAlerts));

      const updatePayload = { alert_id: '1', status: 'unacknowledged', timestamp: new Date().toISOString() };
      act(() => {
        if (mockServer.onmessage) {
          mockServer.onmessage(new MessageEvent('message', {
            data: JSON.stringify({ type: WebSocketMessageTypeEnum.ALERT_STATUS_UPDATE, payload: updatePayload }),
          }));
        }
      });

      expect(result.current.alerts[0].acknowledged).toBe(false);
    });

    it('should handle NODE_CONGESTION_UPDATE', () => {
      const { result } = renderHook(() => useRealtimeUpdates());
      const nodeData: BackendCongestionNodeData[] = [
        { id: 'nodeA', name: 'Node A', latitude: 10, longitude: 20, congestion_score: 55, timestamp: new Date().toISOString() },
        { id: 'nodeB', name: 'Node B', latitude: 12, longitude: 22, congestion_score: 30, timestamp: new Date().toISOString() },
      ];

      const updatePayload = { nodes: nodeData };
      act(() => {
        if (mockServer.onmessage) {
          mockServer.onmessage(new MessageEvent('message', {
            data: JSON.stringify({ type: WebSocketMessageTypeEnum.NODE_CONGESTION_UPDATE, payload: updatePayload }),
          }));
        }
      });

      expect(result.current.nodeCongestionData).toEqual(nodeData);
      expect(result.current.nodeCongestionData).toHaveLength(2);
      expect(result.current.nodeCongestionData?.[0].name).toBe('Node A');
    });
  });

  // Add more tests for connection logic, errors, etc. if time permits
  // For example, test the startWebSocket function:
  it('startWebSocket should attempt to create a WebSocket connection', () => {
    const { result } = renderHook(() => useRealtimeUpdates());
    act(() => {
      result.current.startWebSocket();
    });
    expect(global.WebSocket).toHaveBeenCalledWith(expect.any(String)); // Check if WebSocket constructor was called
  });
});
