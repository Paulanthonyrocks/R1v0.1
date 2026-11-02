import { NextRequest, NextResponse } from 'next/server';

// Server-side geocoding for the map page's "Set Feed Location" search.
// The browser only ever calls this same-origin route; OSM/Photon are
// reached from the Next.js server so CORS never applies and the per-IP
// rate limits (Nominatim: 1 rps) stay on the server box, not the operator's
// browser. No API key needed for either provider.
//
// Providers, in order:
//  1. Photon (photon.komoot.io) - OSM-based, fast, autocomplete-friendly.
//  2. Nominatim (nominatim.openstreetmap.org) - official OSM search, fallback.
// Override with GEOCODE_PROVIDER=photon|nominatim to force one.

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export interface GeocodeResult {
  label: string;
  lat: number;
  lon: number;
  provider: string;
}

const UA = 'R1v0.1-dashboard/1.0 (operator feed-location geocoding)';

async function fetchJson(url: string, timeoutMs = 8000): Promise<unknown> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      headers: { 'User-Agent': UA, Accept: 'application/json' },
      signal: ctrl.signal,
      cache: 'no-store',
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

async function searchPhoton(q: string, limit: number): Promise<GeocodeResult[]> {
  const url = `https://photon.komoot.io/api/?q=${encodeURIComponent(q)}&limit=${limit}`;
  const data: any = await fetchJson(url);
  const out: GeocodeResult[] = [];
  for (const f of data?.features || []) {
    const p = f?.properties || {};
    const coords = f?.geometry?.coordinates || [];
    const lat = Number(p.lat ?? coords[1]);
    const lon = Number(p.lon ?? coords[0]);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
    const label = [p.name, [p.housenumber, p.street].filter(Boolean).join(' '), p.city, p.state, p.country]
      .filter(Boolean)
      .join(', ');
    out.push({ label: label || String(p.name || q), lat, lon, provider: 'photon' });
  }
  return out;
}

async function searchNominatim(q: string, limit: number): Promise<GeocodeResult[]> {
  const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=${limit}&q=${encodeURIComponent(q)}`;
  const data: any = await fetchJson(url);
  const out: GeocodeResult[] = [];
  for (const r of data || []) {
    const lat = Number(r?.lat);
    const lon = Number(r?.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
    out.push({ label: String(r?.display_name || q), lat, lon, provider: 'nominatim' });
  }
  return out;
}

export async function GET(request: NextRequest) {
  const q = (request.nextUrl.searchParams.get('q') || '').trim();
  const rawLimit = parseInt(request.nextUrl.searchParams.get('limit') || '6', 10);
  const limit = Number.isFinite(rawLimit) ? Math.min(10, Math.max(1, rawLimit)) : 6;
  if (q.length < 3) {
    return NextResponse.json({ results: [], error: 'query too short (min 3 chars)' }, { status: 400 });
  }

  const forced = (process.env.GEOCODE_PROVIDER || '').toLowerCase();
  const providers: Array<() => Promise<GeocodeResult[]>> =
    forced === 'nominatim'
      ? [() => searchNominatim(q, limit)]
      : forced === 'photon'
        ? [() => searchPhoton(q, limit)]
        : [() => searchPhoton(q, limit), () => searchNominatim(q, limit)];

  const errors: string[] = [];
  for (const search of providers) {
    try {
      const results = await search();
      if (results.length) return NextResponse.json({ results: results.slice(0, limit) });
    } catch (e: any) {
      errors.push(String(e?.message || e));
    }
  }
  return NextResponse.json(
    { results: [], error: `geocoding unavailable: ${errors.join('; ') || 'no results'}` },
    { status: 502 }
  );
}
