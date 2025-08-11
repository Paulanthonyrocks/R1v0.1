import { useEffect, useMemo } from 'react';
import { APIClient } from '../api/APIClient';
import { useUser } from '../auth/UserContext';

export function useAPI() {
    const { token } = useUser();
    
    const api = useMemo(() => APIClient.getInstance({
        baseURL: process.env.NEXT_PUBLIC_API_URL || process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'
    }), []);

    }, []);

    useEffect(() => {
        if (token) {
            // Token updates are handled automatically by TokenManager
            api.setAuthorizationHeader(`Bearer ${token}`);
        }
    }, [token, api]);

    return api;
}
