import { useState, useEffect, useCallback } from 'react';
import { useRealtimeUpdates } from './hook'; // Assuming hook.ts is in the same directory

const SAMPLE_VIDEO_URL = '/sample-video.mp4';
const MAX_SAMPLE_VIDEO_RETRIES = 3;
const INITIAL_DATA_FETCH_TIMEOUT = 5000; // 5 seconds timeout for initial data fetch

interface UseVideoStreamOptions {
  streamId: string;
  forceSample?: boolean; // Option to force sample video usage
}

const useVideoStream = ({ streamId, forceSample = false }: UseVideoStreamOptions) => {
  // Removed sampleVideoRetriesRef as it was unused
  const { getStreamInfo, isReady } = useRealtimeUpdates();

  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isFetchingInitial, setIsFetchingInitial] = useState(true); // State to track initial API fetch
  const [isLive, setIsLive] = useState(false);

  const fetchSampleVideo = useCallback(async (attempt: number = 1) => {
    if (attempt > MAX_SAMPLE_VIDEO_RETRIES) {
      setError('Failed to load sample video after multiple retries.');
      setIsLoading(false);
      return;
    }

    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 5000); // Timeout for sample video fetch

      const response = await fetch(SAMPLE_VIDEO_URL, { signal: controller.signal });
      clearTimeout(timeoutId);

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      setVideoUrl(SAMPLE_VIDEO_URL);
      setIsLive(false);
      // setIsLoading(false); // Consider if loading should be set to false here if sample is successfully loaded.
                           // It's currently set in fetchInitialStreamInfo's finally block.
    } catch (e: unknown) { // Changed from any to unknown
      console.error(`Failed to fetch sample video on attempt ${attempt}:`, e instanceof Error ? e.message : e);
      // Recursive call, ensure attempt is passed correctly
      setTimeout(() => fetchSampleVideo(attempt + 1), 1000 * attempt); // Exponential backoff
    }
  }, []); // State setters (setError, setIsLoading, setVideoUrl, setIsLive) are stable

  const fetchInitialStreamInfo = useCallback(async () => {
    setIsFetchingInitial(true);
    setIsLoading(true);
    setError(null); // Clear previous errors

    if (forceSample) {
      setVideoUrl(SAMPLE_VIDEO_URL);
      setIsLive(false);
      setIsLoading(false);
      setIsFetchingInitial(false);
      return;
    }

    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), INITIAL_DATA_FETCH_TIMEOUT);

      const apiResponse = await fetch(`/api/v1/streams/${streamId}`, { signal: controller.signal });
      clearTimeout(timeoutId);

      if (apiResponse.ok) {
        const initialStreamInfo = await apiResponse.json();
        if (initialStreamInfo.status === 'live' && initialStreamInfo.liveUrl) {
          setVideoUrl(initialStreamInfo.liveUrl);
          setIsLive(true);
        } else {
          console.log(`API indicates stream ${streamId} is not live. Attempting to load sample video.`);
          fetchSampleVideo(); // No need to pass attempt here, it defaults to 1
          setIsLive(false);
        }
      } else {
        console.warn(`Failed to fetch initial stream info for ${streamId} via API. Status: ${apiResponse.status}`);
        fetchSampleVideo();
        setIsLive(false);
      }
    } catch (err: unknown) { // Changed from any to unknown
      if (err instanceof Error && err.name === 'AbortError') {
        console.warn(`Initial stream info fetch timed out for ${streamId}. Relying on WebSocket updates.`);
      } else {
        console.error(`Error fetching initial stream info for ${streamId}:`, err instanceof Error ? err.message : err);
      }
      fetchSampleVideo();
      setIsLive(false);
    } finally {
      setIsLoading(false);
      setIsFetchingInitial(false);
    }
  }, [streamId, forceSample, fetchSampleVideo]); // Removed stable state setters from dependencies

  useEffect(() => {
    if (isReady) {
      fetchInitialStreamInfo();
    }
    // Adding a cleanup function in case streamId changes or component unmounts
    // while fetchInitialStreamInfo is in progress. This is more advanced
    // and depends on how AbortController is used within fetchInitialStreamInfo.
    // For simplicity, and since fetchInitialStreamInfo creates its own controller,
    // this effect doesn't strictly need a cleanup for AbortController.
  }, [isReady, fetchInitialStreamInfo]);

  useEffect(() => {
    if (isFetchingInitial) {
      return;
    }

    if (forceSample) {
      // If forcing sample, ensure video URL is set to sample and live status is false.
      // This might have been handled by fetchInitialStreamInfo, but good to reinforce.
      if (videoUrl !== SAMPLE_VIDEO_URL) setVideoUrl(SAMPLE_VIDEO_URL);
      if (isLive) setIsLive(false);
      return;
    }

    const streamInfo = getStreamInfo(streamId);

    if (streamInfo) {
      if (streamInfo.status === 'live' && streamInfo.liveUrl) {
        if (videoUrl !== streamInfo.liveUrl) {
          setVideoUrl(streamInfo.liveUrl);
        }
        if (!isLive) {
            setIsLive(true);
        }
      } else {
        if (isLive || videoUrl !== SAMPLE_VIDEO_URL) {
            console.log(`WebSocket update indicates stream ${streamId} is not live. Falling back to sample video.`);
            fetchSampleVideo();
        }
        if (isLive) {
            setIsLive(false);
        }
      }
    } else {
      console.log(`Stream info for ${streamId} not yet available from WebSocket. Current video URL: ${videoUrl}, Live status: ${isLive}`);
      // If no stream info and it was previously live, consider it offline and switch to sample.
      // This prevents staying on a "live" URL that is no longer valid according to WebSocket.
      if (isLive) {
        console.log(`WebSocket lost info for live stream ${streamId}. Falling back to sample video.`);
        fetchSampleVideo();
        setIsLive(false);
      }
    }
  }, [streamId, getStreamInfo, isFetchingInitial, forceSample, fetchSampleVideo, videoUrl, isLive]); // Removed stable state setters

  return { videoUrl, isLoading, error, isLive };
};

export default useVideoStream;