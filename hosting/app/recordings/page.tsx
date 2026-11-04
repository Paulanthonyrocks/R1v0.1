"use client";

import React, { useState, useEffect, useCallback } from 'react';
import AuthGuard from '@/components/auth/AuthGuard';
import DashboardShell from '@/components/dashboard/DashboardShell';
import { UserRole } from '@/lib/auth/roles';
import { videoService, ProcessedVideo } from '@/lib/services/videoService';
import { roadsService } from '@/lib/services/roadsService';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import { parseFeedQuery } from '@/lib/utils/feedSearch';
import { streamMatchesQuery, streamPlacementBadge } from '@/lib/utils/recordingSearch';
import { Road } from '@/lib/types/api';
import { errorNotifier } from '@/lib/utils/errorNotifier';
import { Video, Download, Clock, RefreshCw, Film, Search, MapPin } from 'lucide-react';
import { cn } from '@/lib/utils';

const RecordingsPage = () => {
    const [videos, setVideos] = useState<ProcessedVideo[]>([]);
    const [loading, setLoading] = useState(true);
    const [downloading, setDownloading] = useState<number | null>(null);
    const [query, setQuery] = useState('');
    const { feeds } = useRealtimeUpdates();
    const [roads, setRoads] = useState<Road[]>([]);

    useEffect(() => {
        roadsService.listRoads().then(setRoads).catch(() => setRoads([]));
    }, []);

    // Recordings inherit their camera's placement: filter by road:/dir: like
    // the live map search, and badge each row with road + facing direction.
    const visibleVideos = videos.filter(v => streamMatchesQuery(v, parseFeedQuery(query), feeds, roads));

    const fetchVideos = useCallback(async () => {
        setLoading(true);
        try {
            const list = await videoService.listProcessedVideos({ limit: 100 });
            setVideos(list);
        } catch (e) {
            console.error('Failed to fetch processed videos:', e);
            errorNotifier.error('Failed to load recordings');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- initial backend load on mount; the sanctioned effect use
        fetchVideos();
    }, [fetchVideos]);

    const handleDownload = async (video: ProcessedVideo) => {
        setDownloading(video.id);
        try {
            const blob = await videoService.downloadVideo(video.id);
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = video.file_path.split('/').pop() || `recording_${video.id}.mp4`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
            errorNotifier.success('Recording downloaded');
        } catch (e) {
            console.error('Failed to download recording:', e);
            errorNotifier.error(`Failed to download recording ${video.id}`);
        } finally {
            setDownloading(null);
        }
    };

    const formatDuration = (seconds: number) => {
        if (!Number.isFinite(seconds)) return '—';
        const m = Math.floor(seconds / 60);
        const s = Math.round(seconds % 60);
        return m > 0 ? `${m}m ${s}s` : `${s}s`;
    };

    return (
        <AuthGuard requiredRole={UserRole.VIEWER}>
            <DashboardShell>
                <div className="flex flex-col space-y-6">
                    <div className="flex flex-col sm:flex-row justify-between items-center gap-4">
                        <div>
                            <h1 className="text-3xl font-bold font-lcd matrix-glow tracking-wider text-lcd-text uppercase">Recordings</h1>
                            <p className="text-lcd-text/60 font-lcd italic">Processed video archive from the recording pipeline</p>
                        </div>
                        <div className="flex items-center gap-3 w-full sm:w-auto">
                            <div className="relative flex-1 sm:w-72 font-bold group">
                                <Search className="absolute left-3 top-1/2 -translate-y-1/2 opacity-40 group-focus-within:opacity-100 transition-opacity" size={14} />
                                <input
                                    type="text"
                                    placeholder='FILTER road:"Main St" dir:NE ...'
                                    className="bg-lcd-text/5 border-2 border-lcd-text/30 text-lcd-text rounded-none p-3 pl-9 w-full focus:outline-none focus:border-lcd-text tracking-[0.15em] placeholder:text-lcd-text/20 font-black uppercase text-[10px]"
                                    value={query}
                                    onChange={(e) => setQuery(e.target.value)}
                                />
                            </div>
                            <button
                                onClick={fetchVideos}
                                disabled={loading}
                                className="flex items-center gap-2 px-4 py-2 border-2 border-lcd-text text-lcd-text hover:bg-lcd-text hover:text-lcd-bg font-lcd uppercase text-xs tracking-widest transition-all disabled:opacity-50"
                            >
                                <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
                            </button>
                        </div>
                    </div>

                    {loading && videos.length === 0 ? (
                        <div className="flex flex-col items-center justify-center py-20 text-lcd-text/50">
                            <Film className="h-12 w-12 mb-4 animate-pulse" />
                            <p className="font-lcd text-xl">SCANNING ARCHIVE...</p>
                        </div>
                    ) : videos.length === 0 ? (
                        <div className="text-center py-20 text-lcd-text/50 font-lcd border border-dashed border-lcd-text/30 rounded-lg">
                            <Video className="h-12 w-12 mx-auto mb-4 opacity-20" />
                            <p>Archive Empty. No processed recordings found.</p>
                            <p className="text-[10px] mt-2 opacity-60 uppercase tracking-widest">Record from the Surveillance feed controls</p>
                        </div>
                    ) : (
                        <div className="matrix-card overflow-hidden">
                            <div className="matrix-card-header bg-lcd-text/10 px-4 py-2 text-[10px] font-bold uppercase tracking-widest">
                                {visibleVideos.length} Recording{visibleVideos.length === 1 ? '' : 's'}
                                {query.trim() && <span className="opacity-50">{' // filtered by "' + query + '"'}</span>}
                            </div>
                            <div className="divide-y divide-lcd-text/10">
                                {visibleVideos.map((video) => (
                                    <div key={video.id} className="flex items-center gap-4 px-4 py-3 hover:bg-lcd-text/5 transition-colors">
                                        <div className="p-2 border border-lcd-text/30 text-lcd-text/70">
                                            <Video size={16} />
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <p className="text-sm font-lcd text-lcd-text truncate" title={video.file_path}>
                                                {video.file_path.split('/').pop() || video.stream_id}
                                            </p>
                                            <div className="flex items-center gap-3 text-[10px] opacity-50 font-lcd mt-0.5">
                                                <span className="uppercase tracking-tight">{video.stream_id}</span>
                                                {streamPlacementBadge(video, feeds, roads) && (
                                                    <span className="flex items-center gap-1 text-lcd-green/80">
                                                        <MapPin size={9} /> {streamPlacementBadge(video, feeds, roads)}
                                                    </span>
                                                )}
                                                <span className="flex items-center gap-1">
                                                    <Clock size={9} /> {formatDuration(video.duration)}
                                                </span>
                                                <span>
                                                    {video.start_time ? new Date(video.start_time).toLocaleString() : '—'}
                                                    {video.end_time ? ` → ${new Date(video.end_time).toLocaleTimeString()}` : ''}
                                                </span>
                                            </div>
                                        </div>
                                        <button
                                            onClick={() => void handleDownload(video)}
                                            disabled={downloading !== null}
                                            className={cn(
                                                'flex items-center gap-2 px-3 py-2 border text-[10px] font-lcd uppercase tracking-widest transition-all hover:scale-105 active:scale-95',
                                                downloading === video.id
                                                    ? 'border-lcd-green text-lcd-green animate-pulse'
                                                    : 'border-lcd-text/40 text-lcd-text/80 hover:border-lcd-green hover:text-lcd-green',
                                                downloading !== null && downloading !== video.id && 'opacity-40 cursor-not-allowed'
                                            )}
                                        >
                                            <Download size={12} />
                                            {downloading === video.id ? 'Fetching...' : 'Download'}
                                        </button>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            </DashboardShell>
        </AuthGuard>
    );
};

export default RecordingsPage;
