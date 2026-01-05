"use client";

import React, { useEffect, useState } from 'react';
import DashboardShell from '@/components/dashboard/DashboardShell';
import { LineChart, TrendingUp, AlertCircle, BarChart3, Clock, MapPin } from 'lucide-react';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import PredictiveFlowChart from '@/components/dashboard/PredictiveFlowChart';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

const API_BASE_URL = "";

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
                        const res = await fetch(`${API_BASE_URL}/api/v1/analytics/forecast-vs-actual?lat=${lat}&lon=${lon}&hours=24`);
                        if (res.ok) {                const data = await res.json();
                setComparisonData(data);
            }
        } catch (e) {
            console.error("Failed to fetch comparison:", e);
        } finally {
            setLoading(false);
        }
    };

    return (
        <DashboardShell>
            <div className="flex flex-col space-y-6">
                <div className="flex justify-between items-end border-b-2 border-lcd-text/20 pb-4">
                    <div>
                        <h1 className="text-3xl font-bold font-lcd matrix-glow tracking-wider text-lcd-text uppercase">Predictive Intelligence</h1>
                        <p className="text-lcd-text/60 font-lcd italic text-sm">Forecast vs. Actual Performance Metrics</p>
                    </div>
                    <div className="flex gap-2">
                        {feeds.slice(0, 3).map(f => (
                            <Button 
                                key={f.feed_id}
                                variant="outline" 
                                size="sm"
                                className={cn(
                                    "font-lcd text-[10px] h-8",
                                    selectedFeed === f.feed_id ? "bg-lcd-text text-lcd-bg" : "border-lcd-text/30"
                                )}
                                onClick={() => setSelectedFeed(f.feed_id)}
                            >
                                {f.feed_id.split('_').pop()}
                            </Button>
                        ))}
                    </div>
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
                    {/* Main Chart */}
                    <div className="lg:col-span-3 matrix-card p-6">
                        <div className="flex justify-between items-center mb-6">
                            <h2 className="text-xl font-bold uppercase flex items-center gap-2 font-lcd">
                                <TrendingUp className="text-primary" />
                                Model Accuracy Analysis
                            </h2>
                            <Badge variant="outline" className="font-lcd bg-lcd-text/5 text-primary border-primary/50">
                                LSTM STOCHASTIC ENGINE
                            </Badge>
                        </div>
                        <PredictiveFlowChart data={comparisonData} isLoading={loading} />
                    </div>

                    {/* Stats Sidebar */}
                    <div className="lg:col-span-1 space-y-6">
                        <Card className="matrix-card">
                            <CardHeader className="pb-2">
                                <CardTitle className="text-xs uppercase opacity-60 font-lcd">Current Model Drift</CardTitle>
                            </CardHeader>
                            <CardContent>
                                <div className="text-3xl font-bold font-lcd text-red-500">±4.2%</div>
                                <p className="text-[10px] opacity-40 uppercase mt-1">Variance from ground truth</p>
                            </CardContent>
                        </Card>

                        <Card className="matrix-card">
                            <CardHeader className="pb-2">
                                <CardTitle className="text-xs uppercase opacity-60 font-lcd">Confidence Interval</CardTitle>
                            </CardHeader>
                            <CardContent>
                                <div className="text-3xl font-bold font-lcd text-primary">92%</div>
                                <div className="h-1.5 w-full bg-lcd-text/10 rounded-full mt-2 overflow-hidden">
                                    <div className="h-full bg-primary w-[92%]" />
                                </div>
                            </CardContent>
                        </Card>

                        <div className="matrix-card p-4 space-y-4">
                            <h3 className="text-xs font-bold uppercase border-b border-lcd-text/10 pb-2">Forecast Insights</h3>
                            <div className="space-y-3">
                                <div className="flex gap-2">
                                    <Clock size={14} className="text-primary shrink-0" />
                                    <p className="text-[10px] uppercase leading-tight">Next congestion peak expected at 17:45 (+/- 10m)</p>
                                </div>
                                <div className="flex gap-2">
                                    <AlertCircle size={14} className="text-yellow-500 shrink-0" />
                                    <p className="text-[10px] uppercase leading-tight">Historical variance increasing due to local weather event</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </DashboardShell>
    );
}

// Simple CN helper since I can't import easily sometimes in this env
function cn(...classes: any[]) {
    return classes.filter(Boolean).join(' ');
}
