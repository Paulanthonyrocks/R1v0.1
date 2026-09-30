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

const DEV_DEFAULT = 'http://localhost:8000';

function readEnv(v: string | undefined): string | undefined {
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

let _cachedTunnelPassword: string | null | undefined;

export function getTunnelPassword(): string | null {
  if (_cachedTunnelPassword !== undefined) return _cachedTunnelPassword;
  const pw = process.env.NEXT_PUBLIC_LOCALTUNNEL_PASSWORD;
  _cachedTunnelPassword = pw && pw.trim().length > 0 ? pw.trim() : null;
  return _cachedTunnelPassword;
}

/** REST helper: append `?password=` to a URL string. Idempotent.
 *
 * Host-gated: only appends for loca.lt hosts. Other tunnels (cloudworkstations.dev,
 * ngrok-with-auth, etc.) have their own auth model and reject or mishandle a
 * stray password query param. See ``isLocaLtHost`` for the rationale. */
export function withTunnelPassword(url: string): string {
  const pw = getTunnelPassword();
  if (!pw) return url;
  try {
    const urlObj = new URL(url);
    if (!isLocaLtHost(urlObj)) return url;
    if (!urlObj.searchParams.has('password')) {
      urlObj.searchParams.set('password', pw);
    }
    return urlObj.toString();
  } catch {
    return url;
  }
}

/** WebSocket helper: append `?password=` to a URL object in place. Idempotent.
 *
 * Host-gated: only appends for loca.lt hosts (see ``isLocaLtHost``).
 * Unconditional append was sending the configured secret to non-loca.lt
 * providers (Sep-04: cloudworkstations.dev WS upgrades carried
 * `?password=34.42.239.203` which is the configured env value, not
 * cloudworkstations.dev's auth token -- it should never have been sent). */
export function appendTunnelPassword(url: URL): void {
  const pw = getTunnelPassword();
  if (!pw) return;
  if (!isLocaLtHost(url)) return;
  if (!url.searchParams.has('password')) {
    url.searchParams.set('password', pw);
  }
}

/**
 * NOTE: `sanitizeTunnelUrl` (redacting both `password=` and `tunnel_token=`)
 * is defined further down in this file, next to the other tunnel-secret
 * helpers it must stay in sync with. Do not re-add a second copy here.
 */

/**
 * Returns true when the URL host is a loca.lt tunnel — the only provider
 * that gates unauthenticated requests behind a password query param. Other
 * tunnel providers (cloudworkstations.dev, ngrok with -auth flag, etc.) have
 * their own auth model and will reject or mishandle a stray `?password=`
 * appended to the URL.
 *
 * Without this gate, an env-configured `NEXT_PUBLIC_LOCALTUNNEL_PASSWORD` is
 * unconditionally appended to every WS upgrade URL regardless of host, which:
 *   (a) sends the configured secret to providers that don't expect it
 *       (cloudworkstations.dev: forwards the query to the backend, which may
 *        fail validation or simply ignore it; loca.lt: returns ECONNRESET on
 *        the WS upgrade when the password doesn't match its own session pw).
 *   (b) leaks the secret into logs (Chrome's native ws-failed log can't be
 *       redacted, so the raw password value lands in console output every
 *       reconnect attempt -- observed Sep-04: `?password=34.42.239.203`
 *       appearing 50+ times across one session's reconnect storm).
 */
export function isLocaLtHost(url: URL | string): boolean {
  try {
    const u = typeof url === 'string' ? new URL(url) : url;
    return u.host.endsWith('.loca.lt') || u.host === 'loca.lt';
  } catch {
    return false;
  }
}

// --- Backend tunnel auth token (defence in depth) ---
//
// The loca.lt password above is set to the runner's PUBLIC IP, which means it
// is readable straight out of any tunnel URL. The backend therefore enforces
// its own independent secret (TUNNEL_AUTH_TOKEN / `tunnel_token`), so a leaked
// or guessed IP password still gets a 401 at the application.
//
// Unlike the loca.lt password this is NOT host-gated: the backend gate applies
// to every host it protects, including cloudworkstations.dev. Idempotent.

let _cachedAuthToken: string | null | undefined;

export function getTunnelAuthToken(): string | null {
  if (_cachedAuthToken !== undefined) return _cachedAuthToken;
  const tok = process.env.NEXT_PUBLIC_TUNNEL_AUTH_TOKEN;
  _cachedAuthToken = tok && tok.trim().length > 0 ? tok.trim() : null;
  return _cachedAuthToken;
}

/** REST helper: append `?tunnel_token=`. Idempotent. */
export function withTunnelAuth(url: string): string {
  const tok = getTunnelAuthToken();
  if (!tok) return url;
  try {
    const urlObj = new URL(url);
    if (!urlObj.searchParams.has('tunnel_token')) {
      urlObj.searchParams.set('tunnel_token', tok);
    }
    return urlObj.toString();
  } catch {
    return url;
  }
}

/** WebSocket helper: append `?tunnel_token=`. Idempotent.
 *
 * The query form is the ONLY option for WebSockets -- the browser WS API
 * accepts no custom headers on the handshake. */
export function appendTunnelAuth(url: URL): void {
  const tok = getTunnelAuthToken();
  if (!tok) return;
  if (!url.searchParams.has('tunnel_token')) {
    url.searchParams.set('tunnel_token', tok);
  }
}

/** Redact BOTH tunnel secrets for logging. */
export function sanitizeTunnelUrl(url: string): string {
  if (!url) return url;
  if (url.indexOf('password=') === -1 && url.indexOf('tunnel_token=') === -1) return url;
  try {
    const isAbsolute = /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(url);
    const urlObj = new URL(url, 'http://redact.local');
    if (urlObj.searchParams.has('password')) urlObj.searchParams.set('password', 'REDACTED');
    if (urlObj.searchParams.has('tunnel_token')) urlObj.searchParams.set('tunnel_token', 'REDACTED');
    const out = urlObj.toString();
    return isAbsolute ? out : out.replace('http://redact.local', '');
  } catch {
    return url
      .replace(/([?&]password=)[^&#]*/g, '$1REDACTED')
      .replace(/([?&]tunnel_token=)[^&#]*/g, '$1REDACTED');
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
  const env = readEnv(process.env.NEXT_PUBLIC_API_BASE_URL);
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
  const wsEnv = readEnv(process.env.NEXT_PUBLIC_WS_URL);
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
