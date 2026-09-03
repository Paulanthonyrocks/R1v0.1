import React, { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Fingerprint, Clock, Tag, Database } from 'lucide-react';
import { APIClient } from '@/lib/api/APIClient';
import { getBackendBaseURL } from '@/lib/api/backendBaseUrl';

interface IdentityGalleryProps {
    globalId: string;
    onClose?: () => void;
}

interface GalleryData {
    global_id: string;
    gallery_size: number;
    last_seen: number;
    metadata: Record<string, any>;
    embeddings: number[][];
}

const IdentityGallery: React.FC<IdentityGalleryProps> = ({ globalId, onClose }) => {
    const [loading, setLoading] = useState(true);
    const [data, setData] = useState<GalleryData | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const fetchGallery = async () => {
            setLoading(true);
            try {
                const apiClient = APIClient.getInstance({ baseURL: getBackendBaseURL() });
                const result = await apiClient.get<GalleryData>(
                    `/api/v1/vehicles/global/${globalId}/gallery`
                );
                setData(result);
            } catch (err: any) {
                setError(err.message);
            } finally {
                setLoading(false);
            }
        };

        if (globalId) fetchGallery();
    }, [globalId]);

    if (loading) {
        return (
            <Card className="w-full h-full bg-black/90 border-lcd-bg text-lcd-bg rounded-none font-lcd overflow-hidden">
                <CardHeader className="pb-2">
                    <Skeleton className="h-6 w-1/2 bg-zinc-800" />
                </CardHeader>
                <CardContent className="space-y-4">
                    <Skeleton className="h-24 w-full bg-zinc-800" />
                    <Skeleton className="h-24 w-full bg-zinc-800" />
                </CardContent>
            </Card>
        );
    }

    if (error || !data) {
        return (
            <Card className="w-full h-full bg-black/90 border-red-900/50 text-red-500 rounded-none font-lcd">
                <CardContent className="p-6 flex flex-col items-center justify-center text-center space-y-4">
                    <Database className="w-12 h-12 opacity-50" />
                    <p className="uppercase tracking-widest">{error || "Identity Registry Timeout"}</p>
                    {onClose && (
                        <button onClick={onClose} className="text-xs border border-red-900 bg-red-900/20 px-4 py-1 hover:bg-red-900/40">
                            DISMISS
                        </button>
                    )}
                </CardContent>
            </Card>
        );
    }

    return (
        <Card className="w-full h-full bg-black/95 border-lcd-bg text-lcd-bg rounded-none font-lcd flex flex-col shadow-[0_0_20px_rgba(182,255,176,0.1)]">
            <CardHeader className="p-4 border-b border-lcd-bg/20 flex-shrink-0 flex flex-row items-center justify-between">
                <div>
                    <CardTitle className="text-lg uppercase tracking-[0.2em] flex items-center gap-2">
                        <Fingerprint className="w-4 h-4" />
                        Identity Registry
                    </CardTitle>
                    <p className="text-[10px] opacity-60 font-mono">{globalId}</p>
                </div>
                {onClose && (
                    <button onClick={onClose} className="text-lcd-text hover:text-white transition-colors">
                        ✕
                    </button>
                )}
            </CardHeader>

            <ScrollArea className="flex-grow">
                <CardContent className="p-4 space-y-6">
                    {/* Overview Metadata */}
                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-1">
                            <span className="text-[10px] uppercase opacity-50 flex items-center gap-1">
                                <Tag className="w-3 h-3" /> Classification
                            </span>
                            <Badge variant="outline" className="text-[10px] border-lcd-bg/40 text-lcd-bg bg-lcd-bg/5 rounded-none uppercase">
                                {data.metadata.class_name || 'UNKNOWN'}
                            </Badge>
                        </div>
                        <div className="space-y-1">
                            <span className="text-[10px] uppercase opacity-50 flex items-center gap-1">
                                <Clock className="w-3 h-3" /> Persistence
                            </span>
                            <div className="text-xs">
                                {data.gallery_size} SIGNATURES
                            </div>
                        </div>
                    </div>

                    {/* Appearance Signature Gallery */}
                    <div className="space-y-3">
                        <h3 className="text-xs uppercase tracking-[0.1em] border-l-2 border-lcd-bg pl-2">Signatures</h3>
                        <div className="grid gap-4">
                            {data.embeddings.map((emb, idx) => (
                                <div key={idx} className="p-2 border border-lcd-bg/10 bg-white/[0.02] space-y-2">
                                    <div className="flex justify-between items-center text-[8px] opacity-40 uppercase">
                                        <span>Sample #{idx + 1}</span>
                                    </div>
                                    <div className="h-8 w-full flex items-end gap-[1px]">
                                        {/* Visualize a subset of the embedding vector for display */}
                                        {emb.slice(0, 64).map((val, vidx) => (
                                            <div
                                                key={vidx}
                                                className="flex-grow bg-lcd-bg/40"
                                                style={{ height: `${Math.abs(val) * 100}%` }}
                                            />
                                        ))}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>

                    <div className="text-[9px] opacity-40 bg-white/5 p-2 border-t border-lcd-bg/10 uppercase italic">
                        Persistence data strictly derived from appearance embeddings.
                        Registry TTL: 3600s
                    </div>
                </CardContent>
            </ScrollArea>
        </Card>
    );
};

export default IdentityGallery;
