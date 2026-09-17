"use client";

import React, { useEffect, useState } from 'react';
import { APIClient } from '@/lib/api/APIClient';
import { getBackendBaseURL } from '@/lib/api/backendBaseUrl';

interface AuthenticatedImageProps {
  path: string;
  alt: string;
  className?: string;
}

/** Protected image requests use the same auth/refresh path as JSON requests. */
export default function AuthenticatedImage({ path, alt, className }: AuthenticatedImageProps) {
  const [image, setImage] = useState<{ path: string; url?: string; failed?: boolean } | null>(null);

  useEffect(() => {
    let active = true;
    let objectURL: string | undefined;
    const controller = new AbortController();
    const client = APIClient.getInstance({ baseURL: getBackendBaseURL() });
    client.getBlob(path, { signal: controller.signal })
      .then(blob => {
        if (!active) return;
        objectURL = URL.createObjectURL(blob);
        setImage({ path, url: objectURL });
      })
      .catch(() => {
        if (active) setImage({ path, failed: true });
      });
    return () => {
      active = false;
      controller.abort();
      if (objectURL) URL.revokeObjectURL(objectURL);
    };
  }, [path]);

  if (image?.path !== path) return <span role="status">Loading {alt}...</span>;
  if (image.failed || !image.url) return <span role="status">{alt} unavailable</span>;
  return (
    // A blob URL is already fetched locally; Next Image optimization cannot authenticate this request.
    // eslint-disable-next-line @next/next/no-img-element
    <img src={image.url} alt={alt} className={className} onError={() => setImage({ path, failed: true })} />
  );
}
