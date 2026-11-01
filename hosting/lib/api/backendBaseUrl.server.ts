// Server-only module — uses node:fs, never import from client code.
//
// WHY THIS EXISTS: next.config.ts rewrites() are evaluated ONCE at
// `next dev` startup, so the proxy destination host was baked in from the
// .env.local that existed at boot. When the tunnel subdomain changes
// (cloudflare quick tunnels get a new random host on every restart), the
// client bundle picks up the new NEXT_PUBLIC_WS_URL via HMR, but the
// server-side rewrite kept proxying to the DEAD old host ->
// "Failed to proxy https://<old>.trycloudflare.com/... ENOTFOUND" ->
// plain-text 500 on every /api/v1/* request (observed 2026-11-01:
// dice-workplace-... died, .env.local moved to members-invisible-...).
//
// This module re-reads the .env* files from disk per request (mtime-cached,
// so the common case is one stat per file, no parse), so editing .env.local
// takes effect on the NEXT request with no restart. The file value wins
// over process.env because loadEnvConfig already copied the stale startup
// value into process.env; the file is the live-edited source of truth.

import fs from 'node:fs';
import path from 'node:path';

// Next.js env load order, lowest precedence first, so later assignments win.
const LOAD_ORDER = [
    '.env',
    '.env.production',
    '.env.development',
    '.env.local',
    '.env.production.local',
    '.env.development.local',
];

interface EnvCache {
    newestMtimeMs: number;
    values: Record<string, string>;
}

let cache: EnvCache | null = null;

function readEnvFilesFresh(): Record<string, string> {
    const dir = process.cwd();
    const present: { file: string; mtimeMs: number }[] = [];
    let newestMtimeMs = 0;
    for (const file of LOAD_ORDER) {
        try {
            const st = fs.statSync(path.join(dir, file));
            present.push({ file, mtimeMs: st.mtimeMs });
            if (st.mtimeMs > newestMtimeMs) newestMtimeMs = st.mtimeMs;
        } catch {
            // missing file — fine, Next treats it the same way
        }
    }
    if (cache && cache.newestMtimeMs === newestMtimeMs) return cache.values;

    const values: Record<string, string> = {};
    for (const { file } of present) {
        const text = fs.readFileSync(path.join(dir, file), 'utf8');
        for (const line of text.split('\n')) {
            const m = line.match(/^\s*(?:export\s+)?([A-Za-z0-9_]+)\s*=\s*(.*)$/);
            if (!m) continue;
            let v = m[2].trim();
            if (
                (v.startsWith('"') && v.endsWith('"') && v.length >= 2) ||
                (v.startsWith("'") && v.endsWith("'") && v.length >= 2)
            ) {
                v = v.slice(1, -1);
            }
            values[m[1]] = v;
        }
    }
    cache = { newestMtimeMs, values };
    return values;
}

export function getFreshEnvVar(name: string): string | undefined {
    const v = readEnvFilesFresh()[name];
    return v && v.trim().length > 0 ? v.trim() : undefined;
}

/** Backend base URL as currently written in .env* — fresh per request. */
export function getBackendBaseURLFresh(): string {
    const raw = getFreshEnvVar('NEXT_PUBLIC_API_BASE_URL') ?? process.env.NEXT_PUBLIC_API_BASE_URL;
    const v = (raw ?? '').trim().replace(/\/+$/, '');
    return v || 'http://localhost:8000';
}

/** loca.lt tunnel password as currently written in .env* — fresh per request. */
export function getTunnelPasswordFresh(): string | null {
    return getFreshEnvVar('NEXT_PUBLIC_LOCALTUNNEL_PASSWORD') ?? null;
}

/** Backend tunnel-auth token as currently written in .env* — fresh per request. */
export function getTunnelAuthTokenFresh(): string | null {
    return getFreshEnvVar('NEXT_PUBLIC_TUNNEL_AUTH_TOKEN') ?? null;
}
