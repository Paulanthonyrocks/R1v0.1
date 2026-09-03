"use client";

import React, { useEffect, useState } from 'react';
import DashboardShell from '@/components/dashboard/DashboardShell';
import AuthGuard from '@/components/auth/AuthGuard';
import { LineChart, TrendingUp, AlertCircle, BarChart3, Clock, MapPin } from 'lucide-react';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import PredictiveFlowChart from '@/components/dashboard/PredictiveFlowChart';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import { APIClient } from '@/lib/api/APIClient';
import { getBackendBaseURL } from '@/lib/api/backendBaseUrl';

const API_BASE_URL = getBackendBaseURL();

export default function PredictivePage() {
    const { feeds } = useRealtimeUpdates();
    const [selectedFeed, setSelectedFeed] = useState<string>("");
    const [comparisonData, setComparisonData] = useState<any[]>([]);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (feeds.length > 0 && !selectedFeed) {
            setSelectedFeed(feeds[0].feed_id);
        }
    }, [feeds]);

    useEffect(() => {
        if (selectedFeed) {
            const feed = feeds.find(f => f.feed_id === selectedFeed);
            if (feed && feed.config && feed.config.latitude) {
                fetchComparisonData(feed.config.latitude, feed.config.longitude);
            }
        }
    }, [selectedFeed]);

    const fetchComparisonData = async (lat: number, lon: number) => {
        setLoading(true);
        try {
            const apiClient = APIClient.getInstance({ baseURL: API_BASE_URL });
            const data = await apiClient.get<any[]>(
                `/api/v1/analytics/forecast-vs-actual?lat=${lat}&lon=${lon}&hours=24`
            );
            setComparisonData(data);
        } catch (e) {
            console.error("Failed to fetch comparison:", e);
        } finally {
            setLoading(false);
        }
    };

    return (
        <AuthGuard>
        <DashboardShell>
            <div className="retro-title-container">
                <div className="flex flex-col md:flex-row justify-between items-end gap-6">
                    <div>
                        <h1 className="text-5xl font-black uppercase tracking-tighter font-lcd matrix-glow text-lcd-text mb-1">Predictive Intel</h1>
                        <div className="flex items-center gap-2">
                            <span className="terminal-text text-[10px]">MODEL.STOCHASTIC_ENGINE // FORECAST_HORIZON: 24H</span>
                        </div>
                    </div>
                    <div className="flex bg-lcd-text/5 p-2 border-2 border-lcd-text shadow-inner">
                        <span className="text-[10px] font-black uppercase opacity-40 self-center mr-4 ml-2">Select Node:</span>
                        <div className="flex gap-2">
                            {feeds.slice(0, 5).map(f => (
                                <button 
                                    key={f.feed_id}
                                    className={cn(
                                        "px-4 py-2 font-black text-[10px] uppercase border-2 transition-all",
                                        selectedFeed === f.feed_id 
                                            ? "bg-lcd-text text-lcd-bg border-lcd-text" 
                                            : "border-lcd-text/20 hover:border-lcd-text/50 opacity-60 hover:opacity-100"
                                    )}
                                    onClick={() => setSelectedFeed(f.feed_id)}
                                >
                                    {f.feed_id.slice(-4)}
                                </button>
                            ))}
                        </div>
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-4 gap-12">
                {/* Main Chart */}
                <div className="lg:col-span-3 matrix-card p-0 flex flex-col min-h-[550px]">
                    <div className="matrix-card-header bg-lcd-text/10">
                        <div className="flex items-center gap-2">
                            <TrendingUp size={16} />
                            <span>Actual vs Forecast // Variance Analysis</span>
                        </div>
                        <Badge className="bg-primary text-secondary rounded-none text-[8px] tracking-widest px-2">
                            LSTM_PROB_v4
                        </Badge>
                    </div>
                    <div className="matrix-card-content flex-1 pt-8">
                        <PredictiveFlowChart data={comparisonData} isLoading={loading} />
                    </div>
                </div>

                {/* Stats Sidebar */}
                <div className="lg:col-span-1 space-y-8">
                    <Card className="matrix-card p-0 overflow-hidden">
                        <div className="matrix-card-header">
                            <span>Model Drift</span>
                        </div>
                        <div className="p-6">
                            <div className="text-5xl font-black font-lcd text-red-600 tracking-tighter">±4.2%</div>
                            <p className="text-[10px] font-black opacity-40 uppercase mt-2 tracking-widest">Variance from ground truth</p>
                            <div className="mt-4 pt-4 border-t-2 border-lcd-text/10">
                                <div className="flex justify-between items-center text-[8px] font-black uppercase opacity-60">
                                    <span>Training Epochs</span>
                                    <span>4,200</span>
                                </div>
                            </div>
                        </div>
                    </Card>

                    <Card className="matrix-card p-0 overflow-hidden">
                        <div className="matrix-card-header">
                            <span>Confidence Interval</span>
                        </div>
                        <div className="p-6">
                            <div className="text-5xl font-black font-lcd text-primary tracking-tighter">92%</div>
                            <div className="h-4 w-full bg-lcd-text/10 border-2 border-lcd-text/20 mt-4 overflow-hidden">
                                <div className="h-full bg-primary shadow-[0_0_10px_var(--lcd-text)]" style={{ width: '92%' }} />
                            </div>
                        </div>
                    </Card>

                    <div className="matrix-card p-0 overflow-hidden">
                        <div className="matrix-card-header">
                            <span>Heuristic Insights</span>
                        </div>
                        <div className="p-6 space-y-6">
                            <div className="flex gap-4 p-3 bg-lcd-text/5 border border-lcd-text/10">
                                <Clock size={18} className="text-primary shrink-0" />
                                <p className="text-[10px] font-black uppercase leading-relaxed">
                                    Next congestion peak expected at <span className="text-primary">17:45</span> (+/- 10m)
                                </p>
                            </div>
                            <div className="flex gap-4 p-3 bg-lcd-text/5 border border-lcd-text/10">
                                <AlertCircle size={18} className="text-yellow-600 shrink-0" />
                                <p className="text-[10px] font-black uppercase leading-relaxed opacity-60">
                                    Historical variance increasing due to local event correlation.
                                </p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </DashboardShell>
        </AuthGuard>
    );
}
