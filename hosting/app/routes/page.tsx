"use client";

import React, { useState, useMemo } from 'react';
import AuthGuard from '@/components/auth/AuthGuard';
import DashboardShell from '@/components/dashboard/DashboardShell';
import { UserRole } from '@/lib/auth/roles';
import { useUser } from '@/lib/auth/UserContext';
import { routingService, OptimizedRoute, PersonalizedRouteResponse, RoutePreference, LatLon } from '@/lib/services/routingService';
import { errorNotifier } from '@/lib/utils/errorNotifier';
import { Route, MapPin, Navigation, Sparkles, CloudSun, Zap, Clock, Loader2 } from 'lucide-react';

const RoutePlannerPage = () => {
    const { user } = useUser();
    const [startLat, setStartLat] = useState('34.0522');
    const [startLon, setStartLon] = useState('-118.2437');
    const [endLat, setEndLat] = useState('34.0510');
    const [endLon, setEndLon] = useState('-118.2430');
    const [preference, setPreference] = useState<RoutePreference>('least_traffic');
    const [optimizing, setOptimizing] = useState(false);
    const [personalizing, setPersonalizing] = useState(false);
    const [optimized, setOptimized] = useState<OptimizedRoute | null>(null);
    const [personalized, setPersonalized] = useState<PersonalizedRouteResponse | null>(null);

    const coords = useMemo((): { start: LatLon; end: LatLon } | null => {
        const sl = parseFloat(startLat); const so = parseFloat(startLon);
        const el = parseFloat(endLat); const eo = parseFloat(endLon);
        if ([sl, so, el, eo].some(n => !Number.isFinite(n))) return null;
        return { start: { latitude: sl, longitude: so }, end: { latitude: el, longitude: eo } };
    }, [startLat, startLon, endLat, endLon]);

    const handleOptimize = async () => {
        if (!coords || optimizing) return;
        setOptimizing(true);
        setOptimized(null);
        try {
            const result = await routingService.optimizeRoute({ start_location: coords.start, end_location: coords.end });
            setOptimized(result);
        } catch (e) {
            console.error('Route optimization failed:', e);
            errorNotifier.error('Route optimization failed');
        } finally {
            setOptimizing(false);
        }
    };

    const handlePersonalized = async () => {
        if (!coords || personalizing || !user?.uid) return;
        setPersonalizing(true);
        setPersonalized(null);
        try {
            const result = await routingService.personalizedRoute({
                user_id: user.uid,
                start_location: coords.start,
                end_location: coords.end,
                route_preference: preference,
                consider_weather: true,
                consider_events: true,
                max_alternatives: 3,
            });
            setPersonalized(result);
        } catch (e) {
            console.error('Personalized routing failed:', e);
            errorNotifier.error('Personalized routing failed');
        } finally {
            setPersonalizing(false);
        }
    };

    const inputCls = "w-full px-2 py-1.5 bg-black/30 border border-lcd-text/30 text-lcd-text font-mono text-xs focus:border-lcd-green focus:outline-none";

    return (
        <AuthGuard requiredRole={UserRole.AGENCY}>
            <DashboardShell>
                <div className="flex flex-col space-y-6">
                    <div>
                        <h1 className="text-3xl font-bold font-lcd matrix-glow tracking-wider text-lcd-text uppercase">Route Planner</h1>
                        <p className="text-lcd-text/60 font-lcd italic">Optimize by distance, or route on your learned preferences</p>
                    </div>

                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                        {/* Optimize panel */}
                        <div className="matrix-card p-4">
                            <h2 className="text-sm font-bold uppercase font-lcd tracking-wider text-lcd-text mb-3 flex items-center gap-2">
                                <Route size={14} /> Optimize
                            </h2>
                            <div className="grid grid-cols-2 gap-2 mb-3">
                                <div>
                                    <label className="text-[9px] opacity-40 uppercase block mb-1">Origin lat</label>
                                    <input value={startLat} onChange={e => setStartLat(e.target.value)} className={inputCls} />
                                </div>
                                <div>
                                    <label className="text-[9px] opacity-40 uppercase block mb-1">Origin lon</label>
                                    <input value={startLon} onChange={e => setStartLon(e.target.value)} className={inputCls} />
                                </div>
                                <div>
                                    <label className="text-[9px] opacity-40 uppercase block mb-1">Dest lat</label>
                                    <input value={endLat} onChange={e => setEndLat(e.target.value)} className={inputCls} />
                                </div>
                                <div>
                                    <label className="text-[9px] opacity-40 uppercase block mb-1">Dest lon</label>
                                    <input value={endLon} onChange={e => setEndLon(e.target.value)} className={inputCls} />
                                </div>
                            </div>
                            <button
                                onClick={() => void handleOptimize()}
                                disabled={!coords || optimizing}
                                className="w-full flex items-center justify-center gap-2 px-3 py-2 border-2 border-lcd-text text-lcd-text hover:bg-lcd-text hover:text-lcd-bg font-lcd uppercase text-xs tracking-widest transition-all disabled:opacity-40"
                            >
                                {optimizing ? <Loader2 size={12} className="animate-spin" /> : <Navigation size={12} />}
                                {optimizing ? 'Optimizing...' : 'Optimize Route'}
                            </button>

                            {optimized && (
                                <div className="mt-3 p-3 border-2 border-lcd-text/20 bg-black/10 text-[11px] font-lcd">
                                    <pre className="whitespace-pre-wrap break-words text-[10px] leading-relaxed text-lcd-text/80">
                                        {JSON.stringify(optimized, null, 2).slice(0, 1200)}
                                    </pre>
                                </div>
                            )}
                        </div>

                        {/* Personalized panel */}
                        <div className="matrix-card p-4">
                            <h2 className="text-sm font-bold uppercase font-lcd tracking-wider text-lcd-text mb-3 flex items-center gap-2">
                                <Sparkles size={14} /> Personalized
                            </h2>
                            <div className="mb-3">
                                <label className="text-[9px] opacity-40 uppercase block mb-1">Route preference</label>
                                <select value={preference} onChange={e => setPreference(e.target.value as RoutePreference)} className={inputCls}>
                                    <option value="highways">highways</option>
                                    <option value="scenic">scenic</option>
                                    <option value="shortest">shortest</option>
                                    <option value="least_traffic">least_traffic</option>
                                    <option value="avoid_tolls">avoid_tolls</option>
                                </select>
                            </div>
                            <button
                                onClick={() => void handlePersonalized()}
                                disabled={!coords || personalizing || !user?.uid}
                                className="w-full flex items-center justify-center gap-2 px-3 py-2 border-2 border-lcd-green text-lcd-green hover:bg-lcd-green hover:text-black font-lcd uppercase text-xs tracking-widest transition-all disabled:opacity-40"
                            >
                                {personalizing ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
                                {personalizing ? 'Routing...' : 'Personalized Route'}
                            </button>

                            {personalized && (
                                <div className="mt-3 space-y-2 text-[11px] font-lcd">
                                    <div className="flex items-center gap-2">
                                        <span className="px-2 py-0.5 border border-lcd-green text-lcd-green text-[9px] uppercase">
                                            Confidence {(personalized.confidence_score * 100).toFixed(0)}%
                                        </span>
                                        <span className="opacity-50 text-[9px] uppercase">{personalized.alternative_routes.length} alternatives</span>
                                    </div>
                                    {personalized.weather_impact && (
                                        <div className="p-2 border border-lcd-text/20 bg-black/10 flex items-center gap-2">
                                            <CloudSun size={12} /> <span className="text-[10px]">Weather impact applied</span>
                                        </div>
                                    )}
                                    {personalized.event_impacts.length > 0 && (
                                        <div className="p-2 border border-lcd-text/20 bg-black/10 flex items-center gap-2">
                                            <Zap size={12} /> <span className="text-[10px]">{personalized.event_impacts.length} event impact(s) applied</span>
                                        </div>
                                    )}
                                    <div className="p-3 border-2 border-lcd-text/20 bg-black/10">
                                        <pre className="whitespace-pre-wrap break-words text-[10px] leading-relaxed text-lcd-text/80">
                                            {JSON.stringify(personalized.primary_route, null, 2).slice(0, 1200)}
                                        </pre>
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            </DashboardShell>
        </AuthGuard>
    );
};

export default RoutePlannerPage;
