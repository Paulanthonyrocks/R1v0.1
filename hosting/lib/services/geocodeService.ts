import type { GeocodeResult } from '@/app/api/geocode/route';

class GeocodeService {
  private static instance: GeocodeService;

  private constructor() {}

  public static getInstance(): GeocodeService {
    if (!GeocodeService.instance) {
      GeocodeService.instance = new GeocodeService();
    }
    return GeocodeService.instance;
  }

  /**
   * Search locations via the same-origin /api/geocode proxy (server-side
   * Photon/Nominatim). Used by the map page's "Set Feed Location" panel.
   */
  async search(q: string, limit = 6): Promise<GeocodeResult[]> {
    const res = await fetch(`/api/geocode?q=${encodeURIComponent(q)}&limit=${limit}`, {
      headers: { Accept: 'application/json' },
    });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(body?.error || `geocode HTTP ${res.status}`);
    }
    const data = await res.json();
    return Array.isArray(data?.results) ? data.results : [];
  }
}

export const geocodeService = GeocodeService.getInstance();
export type { GeocodeResult };
