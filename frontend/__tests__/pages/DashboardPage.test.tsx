import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import DashboardPage from '@/app/dashboard/page'; // Adjust path as per your project structure
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import useSWR from 'swr';
import { UserRole } from '@/lib/auth/roles'; // Ensure this path is correct

// Mock AuthGuard to simply render children
jest.mock('@/components/auth/AuthGuard', () => ({ children }: { children: React.ReactNode }) => <>{children}</>);
// Mock UserRole if it's used in a way that needs mocking (e.g. default export)
// jest.mock('@/lib/auth/roles', () => ({ UserRole: { PLANNER: 'planner' }}));


// Mock the useRealtimeUpdates hook
jest.mock('@/lib/hook/useRealtimeUpdates');
const mockUseRealtimeUpdates = useRealtimeUpdates as jest.Mock;

// Mock SWR
jest.mock('swr');
const mockUseSWR = useSWR as jest.Mock;

// Mock AnomalyItem component
jest.mock('@/components/dashboard/AnomalyItem', () => (props: any) => (
  <div data-testid="anomaly-item">
    <p>{props.message}</p>
    <p>{props.timestamp}</p>
    <p>{props.severity}</p>
  </div>
));


describe('DashboardPage', () => {
  const mockStartWebSocket = jest.fn();

  beforeEach(() => {
    // Reset mocks before each test
    mockStartWebSocket.mockClear();
    mockUseRealtimeUpdates.mockReturnValue({
      kpis: { total_flow: 1234, avg_congestion_index: 30, avg_speed_kmh: 60, total_active_incidents: 2 },
      alerts: [
        { id: '1', message: 'Test Alert 1', timestamp: new Date().toISOString(), severity: 'high', details: { location: { latitude: 1, longitude: 1 }} },
        { id: '2', message: 'Test Alert 2', timestamp: new Date().toISOString(), severity: 'medium' },
      ],
      isConnected: true,
      isReady: true,
      startWebSocket: mockStartWebSocket,
      error: null,
      feeds: [],
      setInitialFeeds: jest.fn(),
      setInitialAlerts: jest.fn(),
      sendMessage: jest.fn(),
    });
    mockUseSWR.mockReturnValue({
      data: { // Mock SWR data (e.g., for metrics not from WebSocket or as fallback)
        congestion_index: 25,
        average_speed_kmh: 65,
        active_incidents_count: 1,
        feed_statuses: { running: 3, stopped: 1, error: 0 },
      },
      error: null,
      isLoading: false,
    });
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  it('should call startWebSocket on mount', () => {
    render(<DashboardPage />);
    expect(mockStartWebSocket).toHaveBeenCalledTimes(1);
  });

  it('should display "Total Flow" from kpis', () => {
    render(<DashboardPage />);
    // Look for the MetricCard with title "Total Flow" and check its value
    // This might require specific data-testid attributes on MetricCard title/value elements for robust selection
    const totalFlowValue = screen.getByText('1234'); // Assuming kpis.total_flow is directly rendered
    expect(totalFlowValue).toBeInTheDocument();
    // A more robust way if MetricCard structure is known:
    // const totalFlowCard = screen.getByText('Total Flow').closest('div'); // Find card by title
    // if (totalFlowCard) {
    //   expect(within(totalFlowCard).getByText('1234')).toBeInTheDocument();
    // }
  });

  it('should display other KPI values from SWR or kpis fallback', () => {
    render(<DashboardPage />);
    expect(screen.getByText((content, element) => content.startsWith('25') && element?.textContent?.includes('%'))).toBeInTheDocument(); // Congestion Index from SWR
    // For KPIs that use fallback, ensure the hook's kpi data is rendered if SWR is different or not providing it.
    // Example: if Active Incidents SWR was 1, but hook kpi was 2, it would show 1 (SWR first).
    // If SWR data was metrics?.active_incidents_count ?? kpis?.total_active_incidents
    // and SWR returns 1, and kpis.total_active_incidents is 2, it should show 1.
    // If SWR returns undefined for active_incidents_count, it should show 2.
    expect(screen.getByText((content, element) => content.startsWith('1') && element?.closest('div')?.textContent?.includes('Active Incidents'))).toBeInTheDocument();
  });


  it('should render the correct number of AnomalyItem components', () => {
    render(<DashboardPage />);
    const alertItems = screen.getAllByTestId('anomaly-item');
    expect(alertItems).toHaveLength(2); // Based on 2 alerts in mockUseRealtimeUpdates
  });

  it('should display "No new alerts." when alerts array is empty', () => {
    mockUseRealtimeUpdates.mockReturnValue({
      ...mockUseRealtimeUpdates(), // Spread previous mock return value
      alerts: [], // Override alerts to be empty
      kpis: { total_flow: 1234, avg_congestion_index: 30, avg_speed_kmh: 60, total_active_incidents: 2 },
      isConnected: true,
      isReady: true,
      startWebSocket: mockStartWebSocket,
    });
    render(<DashboardPage />);
    expect(screen.getByText('No new alerts.')).toBeInTheDocument();
  });

  it('should display "Connecting to live alerts..." when not ready', () => {
    mockUseRealtimeUpdates.mockReturnValue({
      ...mockUseRealtimeUpdates(),
      alerts: [],
      kpis: { total_flow: 1234, avg_congestion_index: 30, avg_speed_kmh: 60, total_active_incidents: 2 },
      isConnected: true, // Still connected
      isReady: false, // But not ready
      startWebSocket: mockStartWebSocket,
    });
    render(<DashboardPage />);
    expect(screen.getByText('Connecting to live alerts...')).toBeInTheDocument();
  });

});
