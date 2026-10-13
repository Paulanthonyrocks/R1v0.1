"use client";

import React, { useEffect, useState } from 'react';
import UserPreferencesPanel from '@/components/UserPreferencesPanel';
import DashboardShell from '@/components/dashboard/DashboardShell';
import AuthGuard from '@/components/auth/AuthGuard';
import { UserRole } from '@/lib/auth/roles';
import { routingService, UserRoutingProfile } from '@/lib/services/routingService';

const PreferencesPage: React.FC = () => {
  const [profile, setProfile] = useState<UserRoutingProfile | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial backend load on mount; the sanctioned effect use
    const load = async () => {
      const p = await routingService.getProfile();
      setProfile(p);
      setLoading(false);
    };
    void load();
  }, []);

  return (
    <AuthGuard requiredRole={UserRole.VIEWER}>
      <DashboardShell>
        <UserPreferencesPanel />
        {/* Learned routing profile (GET /api/v1/routes/profile). Read-only view:
            the backend derives it per authed user; there is no save endpoint. */}
        <div className="matrix-card p-4 mt-6">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-bold uppercase font-lcd tracking-wider text-lcd-text">Routing Profile</h2>
            <span className="text-[9px] opacity-40 font-lcd uppercase">read-only // learned</span>
          </div>
          {loading ? (
            <p className="text-[10px] opacity-50 font-lcd animate-pulse">LOADING PROFILE...</p>
          ) : !profile ? (
            <p className="text-[10px] opacity-40 font-lcd py-2 border border-dashed border-lcd-text/20 text-center">
              Profile endpoint unreachable // not enough routing history yet.
            </p>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4 text-[11px] font-lcd">
              <div>
                <label className="text-[9px] opacity-40 uppercase block">Default preference</label>
                <p className="text-lcd-green">{profile.preferences?.default_preference ?? "—"}</p>
              </div>
              <div>
                <label className="text-[9px] opacity-40 uppercase block">Avoid road types</label>
                <p>{profile.preferences?.avoid_road_types?.join(", ") || "none"}</p>
              </div>
              <div>
                <label className="text-[9px] opacity-40 uppercase block">Max tolls</label>
                <p>{profile.preferences?.max_tolls_amount ?? "unlimited"}</p>
              </div>
              <div>
                <label className="text-[9px] opacity-40 uppercase block">Common destinations</label>
                <p>{profile.common_destinations?.length ?? 0}</p>
              </div>
              <div>
                <label className="text-[9px] opacity-40 uppercase block">Favorite routes</label>
                <p>{profile.preferences?.favorite_routes?.length ?? 0}</p>
              </div>
              <div>
                <label className="text-[9px] opacity-40 uppercase block">Last updated</label>
                <p>{profile.last_updated ? new Date(profile.last_updated).toLocaleString() : "—"}</p>
              </div>
            </div>
          )}
        </div>
      </DashboardShell>
    </AuthGuard>
  );
};

export default PreferencesPage;
