import { useEffect, useMemo } from 'react';
import { APIClient } from '../api/APIClient';
import { useUser } from '../auth/UserContext';

export function useAPI() {
    const { token } = useUser();
    
    const api = useMemo(() => APIClient.getInstance({
        baseURL: '/'
    }), []);

    useEffect(() => {
        if (token) {
            // Token updates are handled automatically by TokenManager
            api.setAuthorizationHeader(`Bearer ${token}`);
        }
    }, [token, api]);

    return api;
}
