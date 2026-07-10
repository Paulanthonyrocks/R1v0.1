/**
 * Single source of truth for backend host URLs.
 *
 * Resolution order:
 *   1. Explicit env var (NEXT_PUBLIC_API_BASE_URL for HTTP, NEXT_PUBLIC_WS_URL for raw WS)
 *   2. Same-origin in browser context (fallback at SSR)
 *   3. http://localhost:8000 (development convenience)
 *
 * Centralizing avoids the per-file drift between APIClient's `|| 'http://localhost:8000'`,
 * useAPI's `|| '/'`, and per-page `|| ''`. With the tunnel (loca.lt) + Cloud Workstations
 * split deployment this drift is exactly where things break: an empty string resolves
 * relative to the page's own origin, which fails the loca.lt routing.
 *
 * Use getBackendBaseURL() everywhere `API_BASE_URL` was previously duplicated,
 * and getBackendWsURL('/api/v1/ws') for WebSocket connections.
 */

const ENV_HTTP = 'NEXT_PUBLIC_API_BASE_URL';
const ENV_WS = 'NEXT_PUBLIC_WS_URL';

const DEV_DEFAULT = 'http://localhost:8000';

function readEnv(name: string): string | undefined {
  const v = process.env[name];
  if (typeof v === 'string' && v.trim().length > 0) return v.trim();
  return undefined;
}

function sameOrigin(): string | undefined {
  if (typeof window !== 'undefined' && window.location?.origin) {
    return window.location.origin;
  }
  return undefined;
}

function normalize(value: string | undefined): string {
  if (!value) return DEV_DEFAULT;
  return value.replace(/\/$/, '');
}

/**
 * HTTP base URL used for REST + snapshot asset URLs.
 *
 * Strict env-first semantic: if NEXT_PUBLIC_API_BASE_URL is set we use it
 * unconditionally (no silent rewriting), since Cloud Workstations operators
 * intentionally configure it to point at the loca.lt tunnel.
 *
 * Order:
 *   1. NEXT_PUBLIC_API_BASE_URL
 *   2. window.location.origin (browser only)
 *   3. http://localhost:8000
 */
export function getBackendBaseURL(): string {
  const env = readEnv(ENV_HTTP);
  if (env) return normalize(env);
  const origin = sameOrigin();
  return normalize(origin);
}

/**
 * WS URL for a given path. Mirrors the logic previously embedded in
 * WebSocketProvider.getWsUrl().
 *
 * Order:
 *   1. NEXT_PUBLIC_WS_URL (verbatim, with the path appended)
 *   2. Derive from HTTP base by swapping http→ws / https→wss
 */
export function getBackendWsURL(path: string): string {
  const wsEnv = readEnv(ENV_WS);
  if (wsEnv) {
    const trimmed = wsEnv.replace(/\/$/, '');
    return `${trimmed}${path.startsWith('/') ? path : '/' + path}`;
  }

  const httpBase = getBackendBaseURL();
  let baseUrlObj: URL;
  try {
    baseUrlObj = new URL(httpBase);
  } catch {
    baseUrlObj = new URL(DEV_DEFAULT);
  }
  const wsProtocol = baseUrlObj.protocol === 'https:' ? 'wss:' : 'ws:';
  baseUrlObj.protocol = wsProtocol;
  const cleanPath = path.startsWith('/') ? path : '/' + path;
  return `${baseUrlObj.origin}${cleanPath}`;
}
