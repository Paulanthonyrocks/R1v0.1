import { useMemo } from 'react';
import { APIClient } from '../api/APIClient';
import { getBackendBaseURL } from '../api/backendBaseUrl';

export function useAPI() {
    // Authorization is injected per-request by APIClient from TokenManager
    // (Bearer + 401 refresh). No manual header sync needed here.
    const api = useMemo(() => APIClient.getInstance({
        baseURL: getBackendBaseURL()
    }), []);

    return api;
}
