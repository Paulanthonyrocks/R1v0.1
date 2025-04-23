import { PageLayout, SurveillanceFeed, SurveillanceFeedSkeleton } from '@/components/dashboard';
import { useFeeds } from '@/lib/hook'; // Assuming a hook exists to fetch feeds
import React from 'react';

const SurveillancePage: React.FC = () => {
  const { data: feeds, isLoading, error } = useFeeds(); // Fetch feeds using the hook

  return (
    <PageLayout title="Surveillance Feeds">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {isLoading && (
          <>
            <SurveillanceFeedSkeleton />
            <SurveillanceFeedSkeleton />
            <SurveillanceFeedSkeleton />
          </>
        )}
        {error && <p className="text-red-500 col-span-full">Error loading feeds: {error.message}</p>}
        {feeds && feeds.length === 0 && !isLoading && (
          <p className="text-muted-foreground col-span-full">No active surveillance feeds found.</p>
        )}
        {feeds && feeds.map((feed) => (
          <SurveillanceFeed key={feed.id} feed={feed} />
        ))}
      </div>
    </PageLayout>
  );
};

export default SurveillancePage;