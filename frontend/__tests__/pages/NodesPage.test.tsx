import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import NodesPage from '@/app/nodes/page'; // Adjust path as necessary
import useSWR from 'swr';
import { UserRole } from '@/lib/auth/roles'; // Adjust path as necessary

// Mock AuthGuard
jest.mock('@/components/auth/AuthGuard', () => ({ children }: { children: React.ReactNode }) => <>{children}</>);

// Mock SWR
jest.mock('swr');
const mockUseSWR = useSWR as jest.Mock;

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

  beforeEach(() => {
    // Reset SWR mock before each test
    mockUseSWR.mockReset();
  });

  it('should render CongestionNode components on successful data fetch', () => {
    mockUseSWR.mockReturnValue({
      data: mockNodesData,
      error: null,
      isLoading: false,
    });
    render(<NodesPage />);
    const nodeComponents = screen.getAllByTestId('congestion-node');
    expect(nodeComponents).toHaveLength(mockNodesData.nodes.length);
    expect(screen.getByText('Alpha Node')).toBeInTheDocument();
    expect(screen.getByText('Value: 75%')).toBeInTheDocument(); // congestion_score 75
  });

  it('should display loading message when isLoading is true', () => {
    mockUseSWR.mockReturnValue({
      data: null,
      error: null,
      isLoading: true,
    });
    render(<NodesPage />);
    expect(screen.getByText('Loading Node Data...')).toBeInTheDocument();
  });

  it('should display error message when fetch fails', () => {
    const errorMessage = 'Failed to fetch node congestion data.';
    mockUseSWR.mockReturnValue({
      data: null,
      error: { message: errorMessage, info: { detail: "Network issue" } }, // Mock error with info and status
      isLoading: false,
    });
    render(<NodesPage />);
    expect(screen.getByText('Error Loading Nodes')).toBeInTheDocument();
    expect(screen.getByText(errorMessage)).toBeInTheDocument();
    expect(screen.getByText('Details: Network issue')).toBeInTheDocument();
  });

  it('should display "No nodes are currently reporting data." when data.nodes is empty', () => {
    mockUseSWR.mockReturnValue({
      data: { nodes: [] },
      error: null,
      isLoading: false,
    });
    render(<NodesPage />);
    expect(screen.getByText('No nodes are currently reporting data.')).toBeInTheDocument();
  });


  it('should filter nodes based on search query', async () => {
    mockUseSWR.mockReturnValue({
      data: mockNodesData,
      error: null,
      isLoading: false,
    });
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
