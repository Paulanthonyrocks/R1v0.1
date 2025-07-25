import { useState, useEffect, useCallback } from 'react';
import useVideoSocket from './useVideoSocket';

const SAMPLE_VIDEO_URL = '/sample-video.mp4';

interface UseVideoStreamOptions {
  streamId: string;
  forceSample?: boolean;
}

const useVideoStream = ({ streamId, forceSample = false }: UseVideoStreamOptions) => {
  const { kpis, isConnected, error: wsError } = useVideoSocket(streamId);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isLive, setIsLive] = useState(false);

  useEffect(() => {
    if (forceSample) {
      setVideoUrl(SAMPLE_VIDEO_URL);
      setIsLive(false);
      setIsLoading(false);
      return;
    }

    if (isConnected) {
      // In a real application, you would fetch the stream URL from an API
      // and then use the WebSocket for KPI updates. For this example,
      // we'll just use the sample video URL.
      setVideoUrl(`/api/v1/video/sample-video/stream`);
      setIsLive(true);
      setIsLoading(false);
    } else if (wsError) {
      setError('Failed to connect to video stream');
      setVideoUrl(SAMPLE_VIDEO_URL); // Fallback to sample video
      setIsLive(false);
      setIsLoading(false);
    }
  }, [streamId, forceSample, isConnected, wsError]);

  return { videoUrl, isLoading, error, isLive, kpis };
};

export default useVideoStream;
