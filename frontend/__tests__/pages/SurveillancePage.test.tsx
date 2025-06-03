import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import SurveillancePage from '@/app/surveillance/page'; // Adjust path as necessary
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import { UserRole } from '@/lib/auth/roles'; // Adjust path as necessary

// Mock AuthGuard
jest.mock('@/components/auth/AuthGuard', () => ({ children }: { children: React.ReactNode }) => <>{children}</>);

// Mock useRealtimeUpdates
jest.mock('@/lib/hook/useRealtimeUpdates');
const mockUseRealtimeUpdates = useRealtimeUpdates as jest.Mock;

// Mock SurveillanceFeed component
jest.mock('@/components/dashboard/SurveillanceFeed', () => (props: any) => (
  <div data-testid="surveillance-feed">
    <p>ID: {props.id}</p>
    <p>Name: {props.name}</p>
    <p>Node: {props.node}</p>
    <p>Source: {props.source}</p>
    <p>Status: {props.status}</p>
  </div>
));

describe('SurveillancePage', () => {
  const mockStartWebSocket = jest.fn();
  const mockFeedsData = [
    { id: 'feed1', name: 'Camera Alpha', source: 'rtsp://source1.com', status: 'running', fps: 30 },
    { id: 'feed2', name: 'Camera Bravo', source: 'rtsp://source2.com', status: 'stopped', fps: 0 },
  ];

  beforeEach(() => {
    mockStartWebSocket.mockClear();
    mockUseRealtimeUpdates.mockReturnValue({
      feeds: mockFeedsData,
      isConnected: true,
      isReady: true,
      startWebSocket: mockStartWebSocket,
      alerts: [],
      kpis: null,
      error: null,
      setInitialFeeds: jest.fn(),
      setInitialAlerts: jest.fn(),
      sendMessage: jest.fn(),
    });
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  it('should call startWebSocket on mount if not connected', () => {
    // To specifically test the "if (!isConnected)" condition,
    // we can set isConnected to false initially for one render.
    mockUseRealtimeUpdates.mockReturnValueOnce({
        feeds: [],
        isConnected: false, // Simulate not connected initially
        isReady: false,
        startWebSocket: mockStartWebSocket,
        alerts: [],
        kpis: null,
        error: null,
        setInitialFeeds: jest.fn(),
        setInitialAlerts: jest.fn(),
        sendMessage: jest.fn(),
      });
    render(<SurveillancePage />);
    expect(mockStartWebSocket).toHaveBeenCalledTimes(1);
  });

  it('should render the correct number of SurveillanceFeed components', () => {
    render(<SurveillancePage />);
    const feedComponents = screen.getAllByTestId('surveillance-feed');
    expect(feedComponents).toHaveLength(mockFeedsData.length);
  });

  it('should pass correct props to SurveillanceFeed components', () => {
    render(<SurveillancePage />);

    const firstFeed = mockFeedsData[0];
    expect(screen.getByText(`ID: ${firstFeed.id}`)).toBeInTheDocument();
    expect(screen.getByText(`Name: ${firstFeed.name}`)).toBeInTheDocument();
    expect(screen.getByText(`Node: Source: ${firstFeed.source}`)).toBeInTheDocument(); // Based on current prop mapping
    expect(screen.getByText(`Source: ${firstFeed.source}`)).toBeInTheDocument();
    expect(screen.getByText(`Status: ${firstFeed.status}`)).toBeInTheDocument();

    const secondFeed = mockFeedsData[1];
    expect(screen.getByText(`ID: ${secondFeed.id}`)).toBeInTheDocument();
    // ... and so on for other props of secondFeed
  });

  it('should display "Connecting..." message when isReady is false', () => {
    mockUseRealtimeUpdates.mockReturnValue({
      ...mockUseRealtimeUpdates(), // Get all other default values
      feeds: [],
      isConnected: true, // Can be connected but not yet ready
      isReady: false,
      startWebSocket: mockStartWebSocket,
    });
    render(<SurveillancePage />);
    expect(screen.getByText('Connecting to surveillance system...')).toBeInTheDocument();
  });

  it('should display "Connecting..." message when isConnected is false', () => {
    mockUseRealtimeUpdates.mockReturnValue({
      ...mockUseRealtimeUpdates(),
      feeds: [],
      isConnected: false,
      isReady: false, // Usually if not connected, not ready either
      startWebSocket: mockStartWebSocket,
    });
    render(<SurveillancePage />);
    expect(screen.getByText('Connecting to surveillance system...')).toBeInTheDocument();
  });

  it('should display "No surveillance feeds available" when feeds array is empty and connected/ready', () => {
    mockUseRealtimeUpdates.mockReturnValue({
      ...mockUseRealtimeUpdates(),
      feeds: [], // Empty feeds
      isConnected: true,
      isReady: true,
      startWebSocket: mockStartWebSocket,
    });
    render(<SurveillancePage />);
    expect(screen.getByText('No surveillance feeds available at the moment.')).toBeInTheDocument();
  });
});
