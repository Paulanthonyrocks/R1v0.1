import React, { useEffect } from 'react'; // Added useEffect for consistency, though not strictly needed by test logic
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import NodesPage from '@/app/nodes/page';
// import useSWR from 'swr'; // SWR no longer used
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates'; // Import the hook
import { UserRole } from '@/lib/auth/roles';
import { BackendCongestionNodeData } from '@/lib/types';

// Mock AuthGuard
jest.mock('@/components/auth/AuthGuard', () => ({ children }: { children: React.ReactNode }) => <>{children}</>);

// Mock useRealtimeUpdates
jest.mock('@/lib/hook/useRealtimeUpdates');
const mockUseRealtimeUpdates = useRealtimeUpdates as jest.Mock;

// Mock CongestionNode component
jest.mock('@/components/dashboard/CongestionNode', () => (props: any) => (
  <div data-testid="congestion-node">
    <p>{props.name}</p>
    <p>ID: {props.id}</p>
    <p>Value: {props.value}%</p>
    <p>Updated: {new Date(props.lastUpdated).toLocaleTimeString()}</p>
  </div>
));

describe('NodesPage', () => {
  const mockNodesData = {
    nodes: [
      { id: 'node1', name: 'Alpha Node', latitude: 10, longitude: 10, congestion_score: 75, vehicle_count: 100, average_speed: 30, timestamp: new Date().toISOString() },
      { id: 'node2', name: 'Bravo Node', latitude: 20, longitude: 20, congestion_score: 40, vehicle_count: 50, average_speed: 60, timestamp: new Date().toISOString() },
      { id: 'node3', name: 'Charlie Node', latitude: 30, longitude: 30, congestion_score: 90, vehicle_count: 150, average_speed: 15, timestamp: new Date().toISOString() },
    ]
  };
  const mockStartWebSocket = jest.fn();

  beforeEach(() => {
    // Reset mocks before each test
    mockStartWebSocket.mockClear();
    mockUseRealtimeUpdates.mockReturnValue({
      nodeCongestionData: mockNodesData.nodes, // Provide nodes directly
      isConnected: true,
      isReady: true,
      error: null,
      startWebSocket: mockStartWebSocket,
      // Provide other fields from UseRealtimeUpdatesReturn as needed by the component, or with default mocks
      feeds: [],
      kpis: null,
      alerts: [],
      setInitialFeeds: jest.fn(),
      setInitialAlerts: jest.fn(),
      setInitialKpis: jest.fn(),
      sendMessage: jest.fn(),
      getStreamInfo: jest.fn(),
    });
  });

  it('should call startWebSocket on mount', () => {
    render(<NodesPage />);
    expect(mockStartWebSocket).toHaveBeenCalledTimes(1);
  });

  it('should render CongestionNode components with data from useRealtimeUpdates', () => {
    render(<NodesPage />);
    const nodeComponents = screen.getAllByTestId('congestion-node');
    expect(nodeComponents).toHaveLength(mockNodesData.nodes.length);
    expect(screen.getByText('Alpha Node')).toBeInTheDocument();
    expect(screen.getByText('Value: 75%')).toBeInTheDocument(); // congestion_score 75
  });

  it('should display loading message when isReady is false', () => {
    const currentMockValue = mockUseRealtimeUpdates();
    mockUseRealtimeUpdates.mockReturnValue({
      ...currentMockValue,
      nodeCongestionData: [], // No data yet
      isReady: false,
      isConnected: true, // Could be connected but not ready
    });
    render(<NodesPage />);
    expect(screen.getByText('Connecting to Node Data Stream...')).toBeInTheDocument();
  });

  it('should display error message from WebSocket hook', () => {
    const errorMessage = 'WebSocket connection failed!';
    const currentMockValue = mockUseRealtimeUpdates();
    mockUseRealtimeUpdates.mockReturnValue({
      ...currentMockValue,
      nodeCongestionData: [],
      error: errorMessage,
      isReady: true, // Could be ready but then an error occurs
      isConnected: false,
    });
    render(<NodesPage />);
    expect(screen.getByText('Error Connecting to Node Stream')).toBeInTheDocument();
    expect(screen.getByText(errorMessage)).toBeInTheDocument();
  });

  it('should display "No nodes are currently reporting data via WebSocket." when nodeCongestionData is empty and connected/ready', () => {
    const currentMockValue = mockUseRealtimeUpdates();
    mockUseRealtimeUpdates.mockReturnValue({
      ...currentMockValue,
      nodeCongestionData: [], // Empty data
      isConnected: true,
      isReady: true,
    });
    render(<NodesPage />);
    expect(screen.getByText('No nodes are currently reporting data via WebSocket.')).toBeInTheDocument();
  });

  it('should filter nodes based on search query using data from useRealtimeUpdates', async () => {
    // mockUseRealtimeUpdates is already set up in beforeEach to return mockNodesData.nodes
    render(<NodesPage />);

    const searchInput = screen.getByPlaceholderText('Search nodes by name or ID...');

    // Search for "Alpha"
    fireEvent.change(searchInput, { target: { value: 'Alpha' } });
    await waitFor(() => {
      expect(screen.getByText('Alpha Node')).toBeInTheDocument();
      expect(screen.queryByText('Bravo Node')).not.toBeInTheDocument();
      expect(screen.queryByText('Charlie Node')).not.toBeInTheDocument();
    });

    // Search for "node2" (ID)
    fireEvent.change(searchInput, { target: { value: 'node2' } });
    await waitFor(() => {
      expect(screen.queryByText('Alpha Node')).not.toBeInTheDocument();
      expect(screen.getByText('Bravo Node')).toBeInTheDocument();
      expect(screen.queryByText('Charlie Node')).not.toBeInTheDocument();
    });

    // Search for term that matches no nodes
    fireEvent.change(searchInput, { target: { value: 'Zulu' } });
    await waitFor(() => {
      expect(screen.getByText('No nodes match your search query.')).toBeInTheDocument();
    });
  });
});
