"use client";

import React, { useState, useEffect, useMemo, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import AuthGuard from '@/components/auth/AuthGuard';
import DashboardShell from '@/components/dashboard/DashboardShell';
import { UserRole } from '@/lib/auth/roles';
import { exploreService, NodeCongestionData, TrendSummary, IncidentLikelihoodPrediction, FreeformReport, TrafficDataPoint } from '@/lib/services/exploreService';
import { errorNotifier } from '@/lib/utils/errorNotifier';
import { Activity, TrendingUp, Timer, Leaf, Grid3X3, Zap, MapPin, Sigma, Loader2, Play } from 'lucide-react';
import { cn } from '@/lib/utils';

type Op = 'trends' | 'travel' | 'emissions' | 'nodes' | 'anomaly' | 'predict' | 'locpred' | 'adhoc';

const OPS: { id: Op; label: string; icon: React.ElementType }[] = [
    { id: 'nodes', label: 'Node Congestion', icon: Grid3X3 },
    { id: 'trends', label: 'Trends', icon: TrendingUp },
    { id: 'travel', label: 'Travel Times', icon: Timer },
    { id: 'emissions', label: 'Emissions', icon: Leaf },
    { id: 'predict', label: 'Incident Likelihood', icon: Zap },
    { id: 'locpred', label: 'Location Predictions', icon: MapPin },
    { id: 'anomaly', label: 'Anomaly Detect', icon: Sigma },
    { id: 'adhoc', label: 'Ad-hoc Analysis', icon: Activity },
];

const ExploreContent = () => {
    const params = useSearchParams();
    const opParam = (params?.get('op') as Op | null) ?? null;
    const [op, setOp] = useState<Op>(OPS.some(o => o.id === opParam) ? (opParam as Op) : 'nodes');

    const [loading, setLoading] = useState(false);
    const [nodes, setNodes] = useState<NodeCongestionData[] | null>(null);
    const [trend, setTrend] = useState<TrendSummary | null>(null);
    const [travel, setTravel] = useState<FreeformReport | null>(null);
    const [emissions, setEmissions] = useState<FreeformReport | null>(null);
    const [prediction, setPrediction] = useState<IncidentLikelihoodPrediction | null>(null);
    const [anomalies, setAnomalies] = useState<unknown[] | null>(null);
    const [adhoc, setAdhoc] = useState<string | null>(null);

    // Query inputs
    const [regionId, setRegionId] = useState('region_1');
    const [lat, setLat] = useState('34.0522');
    const [lon, setLon] = useState('-118.2437');
    const [threshold, setThreshold] = useState('0.5');
    const [sensorId, setSensorId] = useState('sensor_1');

    const runOp = async (target: Op) => {
        setLoading(true);
        try {
            if (target === 'nodes') setNodes(await exploreService.nodesCongestion());
            else if (target === 'trends') {
                const end = new Date();
                const start = new Date(end.getTime() - 24 * 3600 * 1000);
                setTrend(await exploreService.getTrends(regionId, start.toISOString(), end.toISOString()));
            } else if (target === 'travel') setTravel(await exploreService.travelTimes(24));
            else if (target === 'emissions') setEmissions(await exploreService.emissions(24));
            else if (target === 'predict') {
                const la = parseFloat(lat), lo = parseFloat(lon);
                if (!Number.isFinite(la) || !Number.isFinite(lo)) throw new Error('Invalid coordinates');
                setPrediction(await exploreService.predictIncidentLikelihood(la, lo));
            } else if (target === 'locpred') {
                const la = parseFloat(lat), lo = parseFloat(lon);
                if (!Number.isFinite(la) || !Number.isFinite(lo)) throw new Error('Invalid coordinates');
                setPrediction(await exploreService.locationPredictions(la, lo));
            } else if (target === 'anomaly') {
                const points: TrafficDataPoint[] = [{ vehicle_count: 40, average_speed: 20 }, { vehicle_count: 42, average_speed: 18 }, { vehicle_count: 80, average_speed: 8 }];
                setAnomalies(await exploreService.detectAnomalies(points));
            }
        } catch (e) {
            console.error(`Explore op ${target} failed:`, e);
            errorNotifier.error(`Query failed: ${e instanceof Error ? e.message : 'unknown error'}`);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- auto-run the cheap GET ops on tab switch
        if (op === 'nodes' && nodes === null) void runOp('nodes');
        // eslint-disable-next-line react-hooks/exhaustive-deps -- run only on op change
    }, [op]);

    const handleAdhoc = async (kind: 'average_traffic' | 'pattern' | 'anomaly' | 'time_series' | 'rolling_averages') => {
        setLoading(true);
        setAdhoc(null);
        try {
            const end = new Date();
            const start = new Date(end.getTime() - 3600 * 1000);
            let result: unknown;
            if (kind === 'average_traffic') result = await exploreService.analyzeAverageTraffic([sensorId], start.toISOString(), end.toISOString());
            else if (kind === 'pattern') result = await exploreService.analyzePattern(sensorId, 'rush_hour');
            else if (kind === 'anomaly') result = await exploreService.analyzeAnomaly({ vehicle_count: 80, average_speed: 8 }, sensorId, parseFloat(threshold) || 0.5);
            else if (kind === 'time_series') result = await exploreService.analyzeTimeSeries([sensorId], start.toISOString(), end.toISOString());
            else result = await exploreService.analyzeRollingAverages([{ t: 1, v: 10 }, { t: 2, v: 12 }], '2');
            setAdhoc(JSON.stringify(result, null, 2));
        } catch (e) {
            console.error(`Ad-hoc ${kind} failed:`, e);
            errorNotifier.error(`Ad-hoc query failed: ${e instanceof Error ? e.message : 'unknown error'}`);
        } finally {
            setLoading(false);
        }
    };

    const inputCls = "w-full px-2 py-1 bg-black/30 border border-lcd-text/30 text-lcd-text font-mono text-[11px] focus:border-lcd-green focus:outline-none";

    return (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
            {/* Sidebar */}
            <div className="md:col-span-1 space-y-1">
                {OPS.map(o => (
                    <button
                        key={o.id}
                        onClick={() => setOp(o.id)}
                        className={cn(
                            'w-full flex items-center gap-2 px-3 py-2 border text-[10px] font-lcd uppercase tracking-widest transition-all',
                            op === o.id
                                ? 'border-lcd-green text-lcd-green bg-lcd-green/10'
                                : 'border-lcd-text/20 text-lcd-text/60 hover:border-lcd-text/50 hover:text-lcd-text'
                        )}
                    >
                        <o.icon size={12} /> {o.label}
                    </button>
                ))}
            </div>

            {/* Content */}
            <div className="md:col-span-3">
                {loading && (
                    <div className="flex items-center gap-2 text-[11px] font-lcd opacity-60 mb-3">
                        <Loader2 size={12} className="animate-spin" /> QUERYING...
                    </div>
                )}

                {op === 'nodes' && (
                    <div className="matrix-card p-4">
                        <h2 className="text-sm font-bold uppercase font-lcd tracking-wider text-lcd-text mb-3">Node Congestion (REST mirror of WS topic)</h2>
                        {nodes === null ? (
                            <button onClick={() => void runOp('nodes')} className="flex items-center gap-2 px-3 py-2 border border-lcd-text text-lcd-text text-[10px] font-lcd uppercase tracking-widest hover:bg-lcd-text hover:text-lcd-bg transition-all">
                                <Play size={10} /> Run Query
                            </button>
                        ) : nodes.length === 0 ? (
                            <p className="text-[10px] opacity-40 font-lcd">No nodes reported.</p>
                        ) : (
                            <div className="overflow-x-auto">
                                <table className="w-full text-[11px] font-lcd">
                                    <thead>
                                        <tr className="text-left opacity-40 uppercase text-[9px] border-b border-lcd-text/20">
                                            <th className="py-1.5 pr-3">Node</th>
                                            <th className="py-1.5 pr-3">Congestion</th>
                                            <th className="py-1.5 pr-3">Vehicles</th>
                                            <th className="py-1.5">Avg Speed</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {nodes.map(n => (
                                            <tr key={n.id} className="border-b border-lcd-text/5">
                                                <td className="py-1.5 pr-3">{n.name || n.id}</td>
                                                <td className={cn("py-1.5 pr-3", (n.congestion_score ?? 0) > 70 ? "text-red-500" : (n.congestion_score ?? 0) > 40 ? "text-yellow-500" : "text-lcd-green")}>
                                                    {(n.congestion_score ?? 0).toFixed(1)}
                                                </td>
                                                <td className="py-1.5 pr-3">{n.vehicle_count ?? '—'}</td>
                                                <td className="py-1.5">{n.average_speed != null ? `${n.average_speed.toFixed(1)} km/h` : '—'}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>
                )}

                {op === 'trends' && (
                    <div className="matrix-card p-4">
                        <h2 className="text-sm font-bold uppercase font-lcd tracking-wider text-lcd-text mb-3">Trend Summary</h2>
                        <div className="grid grid-cols-2 gap-2 mb-3">
                            <input value={regionId} onChange={e => setRegionId(e.target.value)} placeholder="region_id" className={inputCls} />
                        </div>
                        <button onClick={() => void runOp('trends')} className="flex items-center gap-2 px-3 py-2 border border-lcd-text text-lcd-text text-[10px] font-lcd uppercase tracking-widest hover:bg-lcd-text hover:text-lcd-bg transition-all">
                            <Play size={10} /> Run (last 24h)
                        </button>
                        {trend && (
                            <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-3 text-[11px] font-lcd">
                                <div><label className="text-[9px] opacity-40 uppercase block">Avg congestion</label><p className="text-lcd-green">{trend.average_congestion_score?.toFixed(1)}</p></div>
                                <div><label className="text-[9px] opacity-40 uppercase block">Sensors</label><p>{trend.contributing_sensors_count}</p></div>
                                <div><label className="text-[9px] opacity-40 uppercase block">Detections</label><p>{trend.total_vehicle_detections ?? '—'}</p></div>
                                <div><label className="text-[9px] opacity-40 uppercase block">Peak hour</label><p>{trend.peak_hour ?? '—'}</p></div>
                            </div>
                        )}
                    </div>
                )}

                {(op === 'travel' || op === 'emissions') && (
                    <div className="matrix-card p-4">
                        <h2 className="text-sm font-bold uppercase font-lcd tracking-wider text-lcd-text mb-3">{op === 'travel' ? 'Travel Times (24h)' : 'Emissions (24h)'}</h2>
                        <button onClick={() => void runOp(op)} className="flex items-center gap-2 px-3 py-2 border border-lcd-text text-lcd-text text-[10px] font-lcd uppercase tracking-widest hover:bg-lcd-text hover:text-lcd-bg transition-all">
                            <Play size={10} /> Run Query
                        </button>
                        {(op === 'travel' ? travel : emissions) && (
                            <pre className="mt-3 p-3 border-2 border-lcd-text/20 bg-black/10 text-[10px] font-mono whitespace-pre-wrap break-words text-lcd-text/80 max-h-96 overflow-auto">
                                {JSON.stringify(op === 'travel' ? travel : emissions, null, 2).slice(0, 2500)}
                            </pre>
                        )}
                    </div>
                )}

                {(op === 'predict' || op === 'locpred') && (
                    <div className="matrix-card p-4">
                        <h2 className="text-sm font-bold uppercase font-lcd tracking-wider text-lcd-text mb-3">{op === 'predict' ? 'Incident Likelihood' : 'Location Predictions (24h window)'}</h2>
                        <div className="grid grid-cols-2 gap-2 mb-3">
                            <input value={lat} onChange={e => setLat(e.target.value)} placeholder="lat" className={inputCls} />
                            <input value={lon} onChange={e => setLon(e.target.value)} placeholder="lon" className={inputCls} />
                        </div>
                        <button onClick={() => void runOp(op)} className="flex items-center gap-2 px-3 py-2 border border-lcd-text text-lcd-text text-[10px] font-lcd uppercase tracking-widest hover:bg-lcd-text hover:text-lcd-bg transition-all">
                            <Play size={10} /> Run Query
                        </button>
                        {prediction && (
                            <div className="mt-3 space-y-2 text-[11px] font-lcd">
                                <div className="flex items-center gap-3">
                                    <span className={cn("px-2 py-1 border text-[10px] uppercase", (prediction.incident_likelihood ?? 0) > 0.6 ? "border-red-500 text-red-500" : "border-lcd-green text-lcd-green")}>
                                        Likelihood {((prediction.incident_likelihood ?? 0) * 100).toFixed(0)}%
                                    </span>
                                    <span className="opacity-50 text-[10px]">Confidence {((prediction.confidence_score ?? 0) * 100).toFixed(0)}%</span>
                                </div>
                                {prediction.contributing_factors && prediction.contributing_factors.length > 0 && (
                                    <div><label className="text-[9px] opacity-40 uppercase block">Contributing factors</label><p>{prediction.contributing_factors.join(" · ")}</p></div>
                                )}
                                {prediction.recommendations && prediction.recommendations.length > 0 && (
                                    <div><label className="text-[9px] opacity-40 uppercase block">Recommendations</label><p>{prediction.recommendations.join(" · ")}</p></div>
                                )}
                            </div>
                        )}
                    </div>
                )}

                {op === 'anomaly' && (
                    <div className="matrix-card p-4">
                        <h2 className="text-sm font-bold uppercase font-lcd tracking-wider text-lcd-text mb-3">Anomaly Detection (sample 3-point batch)</h2>
                        <button onClick={() => void runOp('anomaly')} className="flex items-center gap-2 px-3 py-2 border border-lcd-text text-lcd-text text-[10px] font-lcd uppercase tracking-widest hover:bg-lcd-text hover:text-lcd-bg transition-all">
                            <Play size={10} /> Run Detection
                        </button>
                        {anomalies !== null && (
                            <pre className="mt-3 p-3 border-2 border-lcd-text/20 bg-black/10 text-[10px] font-mono whitespace-pre-wrap break-words text-lcd-text/80 max-h-96 overflow-auto">
                                {anomalies.length === 0 ? "No anomalies detected in the sample batch." : JSON.stringify(anomalies, null, 2).slice(0, 2000)}
                            </pre>
                        )}
                    </div>
                )}

                {op === 'adhoc' && (
                    <div className="matrix-card p-4">
                        <h2 className="text-sm font-bold uppercase font-lcd tracking-wider text-lcd-text mb-3">Ad-hoc Analysis (analyze/* 5-pack)</h2>
                        <div className="grid grid-cols-2 gap-2 mb-3">
                            <input value={sensorId} onChange={e => setSensorId(e.target.value)} placeholder="sensor_id" className={inputCls} />
                            <input value={threshold} onChange={e => setThreshold(e.target.value)} placeholder="threshold" className={inputCls} />
                        </div>
                        <div className="flex flex-wrap gap-2">
                            {(['average_traffic', 'pattern', 'anomaly', 'time_series', 'rolling_averages'] as const).map(k => (
                                <button key={k} onClick={() => void handleAdhoc(k)} disabled={loading}
                                    className="px-3 py-1.5 border border-lcd-text/40 text-lcd-text/80 text-[10px] font-lcd uppercase tracking-widest hover:border-lcd-green hover:text-lcd-green transition-all disabled:opacity-40">
                                    {k.replace(/_/g, ' ')}
                                </button>
                            ))}
                        </div>
                        {adhoc && (
                            <pre className="mt-3 p-3 border-2 border-lcd-text/20 bg-black/10 text-[10px] font-mono whitespace-pre-wrap break-words text-lcd-text/80 max-h-96 overflow-auto">
                                {adhoc.slice(0, 2500)}
                            </pre>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};

const AnalyticsExplorePage = () => (
    <AuthGuard requiredRole={UserRole.AGENCY}>
        <DashboardShell>
            <div className="flex flex-col space-y-6">
                <div>
                    <h1 className="text-3xl font-bold font-lcd matrix-glow tracking-wider text-lcd-text uppercase">Analytics Explorer</h1>
                    <p className="text-lcd-text/60 font-lcd italic">Ad-hoc queries against the full analytics suite</p>
                </div>
                <Suspense fallback={<p className="text-[10px] opacity-50 font-lcd animate-pulse">LOADING...</p>}>
                    <ExploreContent />
                </Suspense>
            </div>
        </DashboardShell>
    </AuthGuard>
);

export default AnalyticsExplorePage;
