import React from 'react';
import { render, screen, waitFor } from '@testing-library/react'; // waitFor might not be needed if not waiting for async SWR
import '@testing-library/jest-dom';
import DashboardPage from '@/app/dashboard/page';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
// import useSWR from 'swr'; // SWR is no longer used by this page for KPIs
import { UserRole } from '@/lib/auth/roles';

// Mock AuthGuard to simply render children
jest.mock('@/components/auth/AuthGuard', () => ({ children }: { children: React.ReactNode }) => <>{children}</>);

// Mock the useRealtimeUpdates hook
jest.mock('@/lib/hook/useRealtimeUpdates');
const mockUseRealtimeUpdates = useRealtimeUpdates as jest.Mock;

// Mock SWR - No longer needed for this page's KPI data, so can be removed if no other SWR usage
// jest.mock('swr');
// const mockUseSWR = useSWR as jest.Mock; // If other parts of page use SWR, keep minimal mock

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
      kpis: {
        total_flow: 1234,
        congestion_index: 35, // Source of truth for congestion
        average_speed_kmh: 55,  // Source of truth for speed
        active_incidents_count: 3, // Source of truth for incidents
        feed_statuses: { running: 4, error: 0, stopped: 1 } // Example if needed elsewhere, not for main cards
      },
      alerts: [
        { id: '1', message: 'Test Alert 1', timestamp: new Date().toISOString(), severity: 'high', details: { location: { latitude: 1, longitude: 1 }} },
        { id: '2', message: 'Test Alert 2', timestamp: new Date().toISOString(), severity: 'medium' },
      ],
      isConnected: true,
      isReady: true,
      startWebSocket: mockStartWebSocket,
      error: null,
      feeds: [], // Assuming not testing feeds display here
      nodeCongestionData: [], // Assuming not testing node data here
      setInitialFeeds: jest.fn(),
      setInitialAlerts: jest.fn(),
      setInitialKpis: jest.fn(), // If hook has this
      sendMessage: jest.fn(),
      getStreamInfo: jest.fn(), // If hook has this
    });
    // mockUseSWR.mockReturnValue({ // SWR is removed for KPIs
    //   data: {},
    //   error: null,
    //   isLoading: false,
    // });
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

  it('should display Congestion Index, Average Speed, and Active Incidents from kpis', () => {
    render(<DashboardPage />);
    // Check Congestion Index from kpis (35%)
    expect(screen.getByText((content, element) => content === '35' && element?.closest('div')?.textContent?.includes('Congestion Index'))).toBeInTheDocument();
    // Check Average Speed from kpis (55 km/h)
    expect(screen.getByText((content, element) => content === '55' && element?.closest('div')?.textContent?.includes('Average Speed'))).toBeInTheDocument();
    // Check Active Incidents from kpis (3)
    expect(screen.getByText((content, element) => content === '3' && element?.closest('div')?.textContent?.includes('Active Incidents'))).toBeInTheDocument();
  });


  it('should render the correct number of AnomalyItem components', () => {
    render(<DashboardPage />);
    const alertItems = screen.getAllByTestId('anomaly-item');
    expect(alertItems).toHaveLength(2); // Based on 2 alerts in mockUseRealtimeUpdates
  });

  it('should display "No new alerts." when alerts array is empty', () => {
    const currentMockValue = mockUseRealtimeUpdates(); // Get current mock value
    mockUseRealtimeUpdates.mockReturnValue({
      ...currentMockValue, // Spread previous mock return value
      alerts: [], // Override alerts to be empty
      // kpis should still be provided as other cards depend on it
      kpis: currentMockValue.kpis || { total_flow: 1234, congestion_index: 30, average_speed_kmh: 60, active_incidents_count: 2 },
    });
    render(<DashboardPage />);
    expect(screen.getByText('No new alerts.')).toBeInTheDocument();
  });

  it('should display "Connecting to live alerts..." when not ready', () => {
    const currentMockValue = mockUseRealtimeUpdates();
    mockUseRealtimeUpdates.mockReturnValue({
      ...currentMockValue,
      alerts: [],
      // kpis should still be provided
      kpis: currentMockValue.kpis || { total_flow: 1234, congestion_index: 30, average_speed_kmh: 60, active_incidents_count: 2 },
      isConnected: true,
      isReady: false,
    });
    render(<DashboardPage />);
    expect(screen.getByText('Connecting to live alerts...')).toBeInTheDocument();
  });

});
