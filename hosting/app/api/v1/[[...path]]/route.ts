import { NextRequest } from 'next/server';
import { isLocaLtHost } from '@/lib/api/backendBaseUrl';
import {
    getBackendBaseURLFresh,
    getTunnelAuthTokenFresh,
    getTunnelPasswordFresh,
} from '@/lib/api/backendBaseUrl.server';

// Catch-all reverse proxy for /api/v1/*. Replaces the static
// `rewrites()` entry in next.config.ts, which baked the backend host in at
// server start — when the tunnel subdomain changed in .env.local, REST kept
// proxying to the dead host (ENOTFOUND -> 500) until `next dev` was
// restarted. Here the base URL is re-read from .env* per request
// (mtime-cached in backendBaseUrl.server.ts), so a new tunnel URL applies
// on the very next request with no restart.

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Hop-by-hop headers must not be forwarded by a proxy (RFC 9110 §7.6.1).
const HOP_BY_HOP = new Set([
    'connection',
    'keep-alive',
    'proxy-authenticate',
    'proxy-authorization',
    'te',
    'trailer',
    'transfer-encoding',
    'upgrade',
    'host',
]);

async function proxy(request: NextRequest): Promise<Response> {
    const base = getBackendBaseURLFresh();
    // request.url is the same-origin URL the browser called
    // (http://<host>:3000/api/v1/...); keep path + query, swap the origin.
    const target = new URL(base + new URL(request.url).pathname + new URL(request.url).search);

    // loca.lt gates on ?password= and only inspects the URL it is given;
    // for every other provider the param is inert, so only append it for
    // loca.lt hosts (never leaks the secret into proxy logs/URLs elsewhere).
    const pw = getTunnelPasswordFresh();
    if (pw && isLocaLtHost(target) && !target.searchParams.has('password')) {
        target.searchParams.set('password', pw);
    }

    const headers = new Headers();
    request.headers.forEach((value, key) => {
        if (!HOP_BY_HOP.has(key.toLowerCase())) headers.set(key, value);
    });
    headers.set('host', target.host);
    // Browser sends X-Tunnel-Auth itself; if absent (curl, SSR fetch), fall
    // back to the freshly-read token so the gate still passes.
    if (!headers.has('x-tunnel-auth')) {
        const tok = getTunnelAuthTokenFresh();
        if (tok) headers.set('x-tunnel-auth', tok);
    }

    const hasBody = request.method !== 'GET' && request.method !== 'HEAD';
    const upstream = await fetch(target.toString(), {
        method: request.method,
        headers,
        body: hasBody ? await request.arrayBuffer() : undefined,
        // Follow redirects server-side (e.g. FastAPI 307 trailing-slash),
        // matching how the old rewrite proxied them transparently.
        redirect: 'follow',
    });

    const respHeaders = new Headers();
    upstream.headers.forEach((value, key) => {
        const k = key.toLowerCase();
        // Strip hop-by-hop plus content-encoding/content-length: undici
        // already decompressed the body, so forwarding those would corrupt
        // framing (length no longer matches the decoded bytes).
        if (HOP_BY_HOP.has(k) || k === 'content-encoding' || k === 'content-length') return;
        respHeaders.set(key, value);
    });
    // getSetCookie() is the only correct way to copy multi-value set-cookie.
    for (const cookie of upstream.headers.getSetCookie()) {
        respHeaders.append('set-cookie', cookie);
    }

    return new Response(upstream.body, {
        status: upstream.status,
        statusText: upstream.statusText,
        headers: respHeaders,
    });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
export const OPTIONS = proxy;
