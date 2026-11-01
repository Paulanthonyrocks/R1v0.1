import { APIClient } from '../api/APIClient';
import { getApiBrowserBaseURL } from '../api/backendBaseUrl';

export interface GeoCalibrationPoint {
  u: number; // normalized image x [0,1], same space as wire bboxes
  v: number; // normalized image y [0,1]
  lat: number;
  lng: number;
}

class FeedService {
  private static instance: FeedService;
  private apiClient: APIClient;

  private constructor() {
    this.apiClient = APIClient.getInstance({ baseURL: getApiBrowserBaseURL() });
  }

  public static getInstance(): FeedService {
    if (!FeedService.instance) {
      FeedService.instance = new FeedService();
    }
    return FeedService.instance;
  }

  /**
   * PATCH /api/v1/feeds/{feed_id}/config — admin-only. Used by the map page's
   * calibration mode to persist geo_calibration point correspondences.
   */
  async updateFeedConfig(feedId: string, updates: Record<string, unknown>): Promise<unknown> {
    return this.apiClient.patch(`/api/v1/feeds/${feedId}/config`, updates);
  }
}

export const feedService = FeedService.getInstance();
