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

// --- loca.lt tunnel password (shared by REST + WebSocket) ---
//
// loca.lt (and similar tunnel providers) gate unauthenticated requests behind
// a password interstitial. For REST that's an HTTP 503; for WebSockets the
// proxy refuses the upgrade with ECONNRESET. The only programmatic bypass is
// the `?password=` query param. Both APIClient (REST) and WebSocketClient (WS)
// must apply it identically, so the value + bypass logic live here as the
// single source of truth — otherwise the two paths drift and the WS upgrade
// silently dies while REST keeps working.
const ENV_TUNNEL_PASSWORD = 'NEXT_PUBLIC_LOCALTUNNEL_PASSWORD';

let _cachedTunnelPassword: string | null | undefined;

export function getTunnelPassword(): string | null {
  if (_cachedTunnelPassword !== undefined) return _cachedTunnelPassword;
  const pw = process.env[ENV_TUNNEL_PASSWORD];
  _cachedTunnelPassword = pw && pw.trim().length > 0 ? pw.trim() : null;
  return _cachedTunnelPassword;
}

/** REST helper: append `?password=` to a URL string. Idempotent. */
export function withTunnelPassword(url: string): string {
  const pw = getTunnelPassword();
  if (!pw) return url;
  try {
    const urlObj = new URL(url);
    if (!urlObj.searchParams.has('password')) {
      urlObj.searchParams.set('password', pw);
    }
    return urlObj.toString();
  } catch {
    return url;
  }
}

/** WebSocket helper: append `?password=` to a URL object in place. Idempotent. */
export function appendTunnelPassword(url: URL): void {
  const pw = getTunnelPassword();
  if (!pw) return;
  if (!url.searchParams.has('password')) {
    url.searchParams.set('password', pw);
  }
}

/**
 * Redact the tunnel password for logging. The `?password=` query param is
 * mandatory for tunnel deployments (browsers can't set headers on the WS
 * handshake, and loca.lt only honours the query form at its edge), so the
 * secret WILL be in request URLs by design -- but it must never reach logs:
 * console output is persisted to backend/logs and forwarded. Chrome's native
 * "WebSocket connection to '...' failed" line can't be redacted; everything
 * the app itself logs can and must go through here.
 */
export function sanitizeTunnelUrl(url: string): string {
  if (!url || url.indexOf('password=') === -1) return url;
  try {
    const isAbsolute = /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(url);
    const urlObj = new URL(url, 'http://redact.local');
    if (urlObj.searchParams.has('password')) {
      urlObj.searchParams.set('password', 'REDACTED');
    }
    const out = urlObj.toString();
    return isAbsolute ? out : out.replace('http://redact.local', '');
  } catch {
    return url.replace(/([?&]password=)[^&#]*/g, '$1REDACTED');
  }
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
