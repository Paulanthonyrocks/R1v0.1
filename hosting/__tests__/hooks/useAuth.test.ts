import { renderHook, act } from '@testing-library/react';
import { createElement, ReactNode } from 'react';
import { useAuth, AuthProvider } from '../../lib/auth/AuthProvider';
import { auth } from '../../lib/firebase';
import { onAuthStateChanged, User } from 'firebase/auth';
import { TokenManager } from '../../lib/auth/TokenManager';

// Mock Firebase and TokenManager
jest.mock('../../lib/firebase', () => ({
  auth: {},
}));

jest.mock('firebase/auth', () => ({
  onAuthStateChanged: jest.fn(),
}));

jest.mock('../../lib/auth/TokenManager');

const mockOnAuthStateChanged = onAuthStateChanged as jest.Mock;
const mockTokenManagerInstance = {
  updateToken: jest.fn(),
  getCurrentToken: jest.fn(),
  onTokenRefresh: jest.fn(),
};
(TokenManager as unknown as { getInstance: jest.Mock }).getInstance = jest.fn().mockReturnValue(mockTokenManagerInstance);

// The hook reads context — it must render inside the real provider,
// otherwise it returns the default context and the effect never runs.
const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(AuthProvider, null, children);

describe('useAuth Hook', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockOnAuthStateChanged.mockImplementation(() => () => {});
    mockTokenManagerInstance.onTokenRefresh.mockImplementation(() => () => {});
  });

  it('should be in a loading state initially', () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.loading).toBe(true);
    expect(result.current.user).toBeNull();
    expect(result.current.token).toBeNull();
  });

  it('should handle user login', async () => {
    const mockUser = { 
      uid: '123', 
      getIdTokenResult: () => Promise.resolve({ claims: { role: 'admin' } }) 
    } as unknown as User;
    const mockToken = 'test-token';

    mockTokenManagerInstance.getCurrentToken.mockReturnValue(mockToken);
    
    let authCallback: (user: User | null) => void;
    mockOnAuthStateChanged.mockImplementation((auth, callback) => {
      authCallback = callback;
      return () => {};
    });

    const { result } = renderHook(() => useAuth(), { wrapper });

    await act(async () => {
      authCallback(mockUser);
    });

    expect(result.current.loading).toBe(false);
    expect(result.current.user).toBe(mockUser);
    expect(result.current.token).toBe(mockToken);
    expect(mockTokenManagerInstance.updateToken).toHaveBeenCalledWith(mockUser);
  });

  it('should handle user logout', async () => {
    const mockUser = { uid: '123', getIdTokenResult: () => Promise.resolve({ claims: { role: 'viewer' } }) } as unknown as User;
    let authCallback: (user: User | null) => void;
    mockOnAuthStateChanged.mockImplementation((auth, callback) => {
      authCallback = callback;
      return () => {};
    });

    const { result } = renderHook(() => useAuth(), { wrapper });

    await act(async () => {
      authCallback(mockUser);
    });
    
    await act(async () => {
      authCallback(null);
    });

    expect(result.current.loading).toBe(false);
    expect(result.current.user).toBeNull();
    expect(result.current.token).toBeNull();
    expect(mockTokenManagerInstance.updateToken).toHaveBeenCalledWith(null);
  });

  it('should update token on refresh', async () => {
    const mockUser = { uid: '123', getIdTokenResult: () => Promise.resolve({ claims: { role: 'viewer' } }) } as unknown as User;
    const initialToken = 'initial-token';
    const refreshedToken = 'refreshed-token';
    let tokenRefreshCallback: (token: string) => void;

    mockTokenManagerInstance.onTokenRefresh.mockImplementation((callback) => {
      tokenRefreshCallback = callback;
      return () => {};
    });

    mockTokenManagerInstance.getCurrentToken.mockReturnValue(initialToken);
    
    let authCallback: (user: User | null) => void;
    mockOnAuthStateChanged.mockImplementation((auth, callback) => {
      authCallback = callback;
      return () => {};
    });

    const { result } = renderHook(() => useAuth(), { wrapper });

    await act(async () => {
      authCallback(mockUser);
    });

    expect(result.current.token).toBe(initialToken);

    act(() => {
      tokenRefreshCallback(refreshedToken);
    });

    expect(result.current.token).toBe(refreshedToken);
  });
});
