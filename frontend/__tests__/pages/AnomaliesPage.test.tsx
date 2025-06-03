import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import AnomaliesPage from '@/app/anomalies/page'; // Adjust path as necessary
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import axios from 'axios';
import { UserRole } from '@/types/user'; // Adjust path as necessary

// Mock AuthGuard
jest.mock('@/components/auth/AuthGuard', () => ({ children }: { children: React.ReactNode }) => <>{children}</>);

// Mock useRealtimeUpdates
jest.mock('@/lib/hook/useRealtimeUpdates');
const mockUseRealtimeUpdates = useRealtimeUpdates as jest.Mock;

// Mock axios
jest.mock('axios');
const mockedAxios = axios as jest.Mocked<typeof axios>;

// Minimal mock for MapComponent, as it's dynamically loaded and complex
jest.mock('@/components/MapComponent', () => () => <div data-testid="mock-map">Mock Map</div>);


describe('AnomaliesPage', () => {
  const mockStartWebSocket = jest.fn();
  const mockAlerts = [
    { id: '1', type: 'Traffic Jam', severity: 'high', description: 'Severe jam on Main St', timestamp: new Date().toISOString(), details: { location: { latitude: 34.05, longitude: -118.25 } } },
    { id: '2', type: 'Accident', severity: 'medium', description: 'Minor accident on 1st Ave', timestamp: new Date().toISOString(), details: { location: { latitude: 34.06, longitude: -118.30 } } },
  ];

  beforeEach(() => {
    mockStartWebSocket.mockClear();
    mockUseRealtimeUpdates.mockReturnValue({
      alerts: mockAlerts,
      isConnected: true,
      isReady: true,
      startWebSocket: mockStartWebSocket,
      error: null,
      feeds: [],
      kpis: null,
      setInitialFeeds: jest.fn(),
      setInitialAlerts: jest.fn(),
      sendMessage: jest.fn(),
    });
    mockedAxios.patch.mockResolvedValue({ data: {} }); // Default mock for PATCH
    mockedAxios.delete.mockResolvedValue({ data: {} }); // Default mock for DELETE
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  it('should call startWebSocket on mount', () => {
    render(<AnomaliesPage />);
    expect(mockStartWebSocket).toHaveBeenCalledTimes(1);
  });

  it('should render anomaly cards based on mocked alerts data', () => {
    render(<AnomaliesPage />);
    // Check for text that would appear in a MatrixCard for each anomaly
    // The mapAlertDataToAnomaly function might change the 'type' or 'description' slightly.
    // We check for description as it's more likely to be unique.
    expect(screen.getByText('Severe jam on Main St')).toBeInTheDocument();
    expect(screen.getByText('Minor accident on 1st Ave')).toBeInTheDocument();
    // Check if the map is rendered (or its mock)
    expect(screen.getByTestId('mock-map')).toBeInTheDocument();
  });

  it('should call axios.patch when "Resolve" is clicked and update UI optimistically', async () => {
    render(<AnomaliesPage />);

    // Find the "Resolve" button for the first anomaly.
    // This assumes MatrixCard renders a button with text "Resolve".
    // We might need to be more specific if there are many such buttons.
    const resolveButtons = screen.getAllByText('Resolve');
    expect(resolveButtons.length).toBeGreaterThan(0);
    fireEvent.click(resolveButtons[0]); // Click the first "Resolve" button

    // Check if axios.patch was called
    // The ID mapping in mapAlertDataToAnomaly might be an issue here if it's not just parseInt.
    // For mockAlerts[0].id = '1', parseInt('1',10) is 1.
    await waitFor(() => {
      // Verify the new endpoint and request body
      expect(mockedAxios.patch).toHaveBeenCalledWith('/api/alerts/1/acknowledge', { acknowledged: true });
    });

    // Check for optimistic UI update (e.g., "(Resolved)" text appears)
    // This depends on how MatrixCard displays resolved status.
    // Assuming the card for "Severe jam on Main St" is the first one.
    const firstAnomalyCard = screen.getByText('Severe jam on Main St').closest('.transition-all'); // Find parent card
    expect(firstAnomalyCard).toBeInTheDocument();
    if(firstAnomalyCard) {
        // This test needs the component to actually re-render with the "(Resolved)" text.
        // The local state update `setAllAnomalies` should trigger this.
        await waitFor(() => {
             expect(screen.getByText((content, element) => element?.textContent === 'Severity: High(Resolved)' || content.includes('(Resolved)'))).toBeInTheDocument();
        });
    }
  });


  it('should call axios.delete when "Dismiss" is clicked and remove item optimistically', async () => {
    render(<AnomaliesPage />);

    const initialAlertCount = mockAlerts.length;
    expect(screen.getByText('Severe jam on Main St')).toBeInTheDocument(); // Ensure it's there initially

    const dismissButtons = screen.getAllByText('Dismiss');
    expect(dismissButtons.length).toBeGreaterThan(0);
    fireEvent.click(dismissButtons[0]); // Click dismiss for the first anomaly

    await waitFor(() => {
      // Verify the new endpoint
      expect(mockedAxios.delete).toHaveBeenCalledWith('/api/alerts/1');
    });

    // Check for optimistic UI update (item removed)
    // The item with 'Severe jam on Main St' should be gone.
    await waitFor(() => {
        expect(screen.queryByText('Severe jam on Main St')).not.toBeInTheDocument();
    });
    // Also check that other items remain, if applicable, or the count of cards.
    // This depends on how you identify the cards. If MatrixCard has a common testid or role.
  });

  it('should display loading state when isReady is false', () => {
    const currentMockValue = mockUseRealtimeUpdates();
    mockUseRealtimeUpdates.mockReturnValue({
      ...currentMockValue,
      alerts: [], // Provide default or empty alerts
      kpis: currentMockValue.kpis, // Preserve other state parts
      feeds: currentMockValue.feeds,
      nodeCongestionData: currentMockValue.nodeCongestionData,
      error: currentMockValue.error,
      isConnected: true,
      isReady: false, // Simulate not ready
      startWebSocket: mockStartWebSocket,
    });
    render(<AnomaliesPage />);
    expect(screen.getByText('Loading...')).toBeInTheDocument(); // From the <Loading /> component
  });

  it('should display error state when wsError is present', () => {
    const errorMessage = "WebSocket connection failed";
    const currentMockValue = mockUseRealtimeUpdates();
    mockUseRealtimeUpdates.mockReturnValue({
      ...currentMockValue,
      alerts: [],
      kpis: currentMockValue.kpis,
      feeds: currentMockValue.feeds,
      nodeCongestionData: currentMockValue.nodeCongestionData,
      isConnected: false,
      isReady: true,
      startWebSocket: mockStartWebSocket,
      error: { message: errorMessage } as any,
    });
    render(<AnomaliesPage />);
    expect(screen.getByText(`Error connecting to real-time updates: ${errorMessage}. Please try again later.`)).toBeInTheDocument();
  });

});
