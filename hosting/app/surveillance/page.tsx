'use client';

import React, { useState, useMemo } from 'react';
import AuthGuard from '@/components/auth/AuthGuard';
import { UserRole } from '@/lib/auth/roles';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';

import SurveillanceMatrix from '@/components/dashboard/SurveillanceMatrix';
import SurveillanceSummary from '@/components/dashboard/SurveillanceSummary';
import { FeedStatusData } from '@/lib/types';
import { Search, Play, Square, RefreshCw, Bookmark, BookmarkCheck, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import DashboardShell from '@/components/dashboard/DashboardShell';
import AddFeedDialog from '@/components/dashboard/AddFeedDialog';
import { PlateCheck, checkPlate, fetchPlateLists } from '@/lib/safetyHub';
import { roadsService } from '@/lib/services/roadsService';
import { presetService } from '@/lib/services/presetService';
import { parseFeedQuery, matchesFeed, resolveRoad } from '@/lib/utils/feedSearch';
import { Road, Preset } from '@/lib/types/api';

const SurveillancePage = () => {
  const { feeds, isConnected, isReady, startFeed, stopFeed } = useRealtimeUpdates();
  const [searchQuery, setSearchQuery] = useState('');
  const [roads, setRoads] = useState<Road[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [presetName, setPresetName] = useState('');
  const [activePresetId, setActivePresetId] = useState<string | null>(null);

  React.useEffect(() => {
    roadsService.listRoads().then(setRoads).catch(() => setRoads([]));
    presetService.listPresets().then(setPresets).catch(() => setPresets([]));
  }, []);

  // Save the CURRENT filter result as a named warden preset (one-click layout).
  const handleSavePreset = async () => {
    const name = presetName.trim();
    if (!name || filteredFeeds.length === 0) return;
    const road = resolveRoad(parseFeedQuery(searchQuery).road, roads);
    try {
      const p = await presetService.createPreset({
        name,
        feed_ids: filteredFeeds.map(f => f.feed_id),
        road_id: road?.id ?? null,
      });
      setPresets(prev => [...prev.filter(x => x.id !== p.id), p].sort((a, b) => a.name.localeCompare(b.name)));
      setActivePresetId(p.id);
      setPresetName('');
    } catch (e) {
      console.error('[Surveillance] Save preset failed:', e);
    }
  };

  const handleDeletePreset = async (id: string) => {
    try {
      await presetService.deletePreset(id);
      setPresets(prev => prev.filter(p => p.id !== id));
      if (activePresetId === id) setActivePresetId(null);
    } catch (e) {
      console.error('[Surveillance] Delete preset failed:', e);
    }
  };
  // ANPR allow/block check (feature 8). Honest empty until backend enabled.
  const [plate, setPlate] = useState('');
  const [plateResult, setPlateResult] = useState<PlateCheck | null>(null);
  const [plateChecking, setPlateChecking] = useState(false);
  const [plateLists, setPlateLists] = useState<{ allow: string[]; block: string[] }>({ allow: [], block: [] });

  const runPlateCheck = async () => {
    const value = plate.trim();
    if (!value || plateChecking) return;
    setPlateChecking(true);
    try {
      setPlateResult(await checkPlate(value));
      setPlateLists(await fetchPlateLists());
    } catch (error) {
      console.error('[Surveillance] Plate check failed:', error);
      setPlateResult(null);
    } finally {
      setPlateChecking(false);
    }
  };

  const queryFeeds = useMemo(() => {
    if (!searchQuery.trim()) return feeds;
    // Placement-aware query: free text plus road:/dir:/status: tokens.
    const q = parseFeedQuery(searchQuery);
    return feeds.filter(feed => matchesFeed(feed, q, roads));
  }, [feeds, searchQuery, roads]);

  // An applied warden preset wins: one click restores its exact camera group.
  const activePreset = presets.find(p => p.id === activePresetId);
  const filteredFeeds = activePreset
    ? feeds.filter(f => activePreset.feed_ids.includes(f.feed_id))
    : queryFeeds;

  const handleStartAll = () => {
    const eligible = feeds.filter(f => f.status === 'stopped' || f.status === 'error');
    if (eligible.length === 0) {
      console.warn('[Surveillance] Global Start: no feeds in stopped or error state.');
      return;
    }
    eligible.forEach(f => startFeed(f.feed_id));
  };

  const handleStopAll = () => {
    const eligible = feeds.filter(f =>
      f.status === 'running' || f.status === 'starting' || f.status === 'stopping'
    );
    if (eligible.length === 0) {
      console.warn('[Surveillance] Global Stop: no feeds in running, starting, or stopping state.');
      return;
    }
    eligible.forEach(f => stopFeed(f.feed_id));
  };

  const renderContent = () => {
    if (!isReady || !isConnected) {
      return (
        <div className="flex flex-col items-center justify-center h-96 opacity-50 text-lcd-text">
            <RefreshCw className="animate-spin mb-4" size={48} />
            <p className="tracking-[0.2em] font-bold uppercase">Negotiating Handshake...</p>
        </div>
      );
    }

    return (
      <div>
        <SurveillanceSummary feeds={feeds} />
        <SurveillanceMatrix feeds={filteredFeeds} />
      </div>
    );
  };

  return (
    <AuthGuard requiredRole={UserRole.AGENCY}>
      <DashboardShell>
          <div className="retro-title-container">
              <div className="flex flex-col lg:flex-row justify-between items-end gap-6">
                  <div>
                      <h1 className="text-5xl font-black uppercase tracking-tighter font-lcd matrix-glow text-lcd-text mb-1">Surveillance Matrix</h1>
                      <div className="flex items-center gap-2">
                          <span className="terminal-text text-[10px]">UPLINK.SECURE // LIVE_VIDEO_PROCESSOR_v8</span>
                      </div>
                  </div>
                  
                  <div className="flex flex-col sm:flex-row gap-4 w-full lg:w-auto items-center">
                      <div className="relative flex-1 sm:w-80 font-bold group">
                          <Search className="absolute left-3 top-1/2 -translate-y-1/2 opacity-40 group-focus-within:opacity-100 transition-opacity" size={18} />
                          <input
                            type="text"
                            placeholder='SEARCH FEEDS... road:"Main St" dir:NE status:running'
                            className="bg-lcd-text/5 border-2 border-lcd-text/30 text-lcd-text rounded-none p-4 pl-12 w-full focus:outline-none focus:border-lcd-text focus:bg-lcd-text/10 tracking-[0.2em] placeholder:text-lcd-text/20 font-black uppercase transition-all"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                          />
                      </div>
                      <div className="flex gap-3">
                          <AddFeedDialog />
                          <Button 
                            onClick={handleStartAll}
                            className="matrix-btn-sleek h-12 px-6 bg-green-500/10 text-green-700 border-green-600 hover:bg-green-500 hover:text-white"
                          >
                              <Play size={16} className="mr-2" /> Global Start
                          </Button>
                          <Button 
                            onClick={handleStopAll}
                            className="matrix-btn-sleek h-12 px-6 bg-red-500/10 text-red-700 border-red-600 hover:bg-red-500 hover:text-white"
                          >
                              <Square size={16} className="mr-2" /> Global Stop
                          </Button>
                      </div>
                  </div>
              </div>
          </div>

          <div className="matrix-card p-0 overflow-hidden mt-4">
              <div className="matrix-card-header bg-lcd-text/10 flex items-center gap-2">
                  <Bookmark size={14} />
                  <span>Warden Presets // one-click camera groups</span>
              </div>
              <div className="p-4 bg-lcd-text/5 flex flex-col gap-3">
                  <div className="flex gap-3">
                      <input
                        type="text"
                        placeholder="PRESET NAME (saves current filter: N feeds)..."
                        className="bg-lcd-text/5 border-2 border-lcd-text/30 text-lcd-text rounded-none p-3 flex-1 focus:outline-none focus:border-lcd-text tracking-[0.2em] placeholder:text-lcd-text/20 font-black uppercase text-xs"
                        value={presetName}
                        onChange={(e) => setPresetName(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') void handleSavePreset(); }}
                      />
                      <Button onClick={() => void handleSavePreset()} disabled={!presetName.trim() || filteredFeeds.length === 0} className="matrix-btn-sleek px-6">
                          SAVE {filteredFeeds.length} FEEDS
                      </Button>
                  </div>
                  {presets.length > 0 && (
                      <div className="flex flex-wrap gap-2">
                          {presets.map(p => (
                              <div key={p.id} className={`flex items-center gap-1 border px-3 py-1.5 text-[10px] font-lcd uppercase tracking-widest ${activePresetId === p.id ? 'border-lcd-green text-lcd-green bg-lcd-green/10' : 'border-lcd-text/30 text-lcd-text/70'}`}>
                                  <button onClick={() => setActivePresetId(activePresetId === p.id ? null : p.id)} className="flex items-center gap-1.5 hover:opacity-80" title={`${p.feed_ids.length} feeds${p.road_id ? ` · ${p.road_id}` : ''}`}>
                                      {activePresetId === p.id ? <BookmarkCheck size={12} /> : <Bookmark size={12} />}
                                      {p.name} ({p.feed_ids.length})
                                  </button>
                                  <button onClick={() => void handleDeletePreset(p.id)} className="opacity-50 hover:opacity-100 hover:text-red-500"><X size={11} /></button>
                              </div>
                          ))}
                      </div>
                  )}
              </div>
          </div>

          <div className="min-h-[600px] flex flex-col">
              {renderContent()}
          </div>

          <div className="matrix-card p-0 overflow-hidden mt-8">
              <div className="matrix-card-header bg-lcd-text/10">
                  <div className="flex items-center gap-2">
                      <Search size={14} />
                      <span>ANPR Allow/Block Check // Plate Registry</span>
                  </div>
              </div>
              <div className="p-6 bg-lcd-text/5 flex flex-col md:flex-row gap-4 items-stretch md:items-center">
                  <input
                    type="text"
                    placeholder="ENTER PLATE..."
                    className="bg-lcd-text/5 border-2 border-lcd-text/30 text-lcd-text rounded-none p-4 flex-1 focus:outline-none focus:border-lcd-text tracking-[0.2em] placeholder:text-lcd-text/20 font-black uppercase"
                    value={plate}
                    onChange={(e) => setPlate(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') void runPlateCheck(); }}
                  />
                  <Button
                    onClick={() => void runPlateCheck()}
                    disabled={!plate.trim() || plateChecking}
                    className="matrix-btn-sleek h-14 px-8"
                  >
                      {plateChecking ? 'CHECKING...' : 'CHECK PLATE'}
                  </Button>
              </div>
              {plateResult && (
                  <div className="px-6 pb-6 bg-lcd-text/5">
                      <div className="p-4 border-2 border-lcd-text/20 bg-black/10 text-[11px] font-bold uppercase tracking-wider">
                          {plateResult.unconfigured ? (
                              <span className="opacity-60">ANPR unconfigured // recognizer disabled</span>
                          ) : plateResult.blocked ? (
                              <span className="text-red-600">BLOCKED // {plateResult.plate}</span>
                          ) : plateResult.allowed ? (
                              <span className="text-green-700">ALLOWED // {plateResult.plate}</span>
                          ) : (
                              <span className="opacity-60">Unknown plate // {plateResult.plate}</span>
                          )}
                          {(plateLists.allow.length > 0 || plateLists.block.length > 0) && (
                              <span className="opacity-60">{' // Registry: '}{plateLists.allow.length} allow, {plateLists.block.length} block</span>
                          )}
                      </div>
                  </div>
              )}
          </div>
      </DashboardShell>
    </AuthGuard>
  );
};


export default SurveillancePage;