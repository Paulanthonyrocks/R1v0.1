"use client";

import { useState, useEffect, useMemo } from "react";
import MatrixCard from "@/components/MatrixCard";
import AuthGuard from "@/components/auth/AuthGuard";
import DashboardShell from "@/components/dashboard/DashboardShell";
import MatrixButton from "@/components/MatrixButton";
import { UserRole } from "@/lib/auth/roles";
import { cn } from "@/lib/utils";
import { APIClient } from "@/lib/api/APIClient";
import { getBackendBaseURL } from "@/lib/api/backendBaseUrl";

const API_BASE_URL = getBackendBaseURL();

type Format = "CSV" | "JSON";
type DataSet = "Incidents" | "Feed Statuses";
type Range = "Last 24h" | "Last Week" | "All";

const RANGE_MS: Record<Range, number> = {
  "Last 24h": 24 * 60 * 60 * 1000,
  "Last Week": 7 * 24 * 60 * 60 * 1000,
  "All": Number.POSITIVE_INFINITY,
};

const toCSV = (rows: Record<string, unknown>[]): string => {
  if (rows.length === 0) return "";
  const headers = Object.keys(rows[0]);
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [headers.join(","), ...rows.map((r) => headers.map((h) => esc(r[h])).join(","))].join("\n");
};

const unwrap = <T,>(payload: unknown): T[] => {
  if (Array.isArray(payload)) return payload as T[];
  if (payload && typeof payload === "object" && Array.isArray((payload as { data?: unknown }).data)) {
    return (payload as { data: T[] }).data;
  }
  return [];
};

const ExportPage: React.FC = () => {
  const [loading, setLoading] = useState<boolean>(true);
  const [fetching, setFetching] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedFormat, setSelectedFormat] = useState<Format>("CSV");
  const [selectedData, setSelectedData] = useState<DataSet[]>(["Incidents"]);
  const [timeRange, setTimeRange] = useState<Range>("Last 24h");
  const [incidents, setIncidents] = useState<Record<string, unknown>[]>([]);
  const [feeds, setFeeds] = useState<Record<string, unknown>[]>([]);
  const [exportedAt, setExportedAt] = useState<string | null>(null);
  // Newest record timestamp; the time-range filter is relative to the data,
  // not the wall clock, so filtering stays pure during render.
  const [cutoff, setCutoff] = useState<number>(0);

  useEffect(() => {
    const timer = setTimeout(() => setLoading(false), 500);
    return () => clearTimeout(timer);
  }, []);

  const fetchAll = async () => {
    setFetching(true);
    setError(null);
    try {
      const apiClient = APIClient.getInstance({ baseURL: API_BASE_URL });
      const [inc, fd] = await Promise.all([
        apiClient.get<unknown>("/api/v1/incidents"),
        apiClient.get<unknown>("/api/v1/feeds/"),
      ]);
      const incRows = unwrap<Record<string, unknown>>(inc);
      const feedRows = unwrap<Record<string, unknown>>(fd);
      setIncidents(incRows);
      setFeeds(feedRows);
      // Anchor the time-range filter to the newest record instead of the
      // wall clock: pure, deterministic, and robust to stale backend data.
      const toTs = (r: Record<string, unknown>): number => {
        const v = r.created_at ?? r.timestamp ?? r.last_seen;
        const t = v ? new Date(v as string | number).getTime() : NaN;
        return Number.isNaN(t) ? 0 : t;
      };
      setCutoff(incRows.reduce((m, r) => Math.max(m, toTs(r)), 0));
    } catch (e) {
      console.error("Export fetch failed:", e);
      setError("Failed to fetch data from backend. Check uplink and retry.");
    } finally {
      setFetching(false);
    }
  };

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial backend load on mount, same pattern as sibling pages
    fetchAll();
  }, []);

  const inRange = (row: Record<string, unknown>): boolean => {
    const window = RANGE_MS[timeRange];
    if (!Number.isFinite(window)) return true;
    const ts = row.created_at ?? row.timestamp ?? row.last_seen;
    if (!ts) return true;
    const t = new Date(ts as string | number).getTime();
    if (Number.isNaN(t)) return true;
    return cutoff - t <= window;
  };

  const rows = useMemo(() => {
    const out: Record<string, unknown>[] = [];
    if (selectedData.includes("Incidents")) {
      for (const inc of incidents.filter(inRange)) out.push({ dataset: "incident", ...inc });
    }
    if (selectedData.includes("Feed Statuses")) {
      for (const f of feeds) out.push({ dataset: "feed", ...f });
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incidents, feeds, selectedData, timeRange, cutoff]);

  const preview = useMemo(() => rows.slice(0, 5), [rows]);

  const handleExport = () => {
    if (rows.length === 0) return;
    const content =
      selectedFormat === "CSV" ? toCSV(rows) : JSON.stringify(rows, null, 2);
    const blob = new Blob([content], {
      type: selectedFormat === "CSV" ? "text/csv" : "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `route-one-export-${Date.now()}.${selectedFormat.toLowerCase()}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setExportedAt(new Date().toLocaleTimeString());
  };

  const toggleData = (data: DataSet) => {
    setSelectedData((prev) =>
      prev.includes(data) ? prev.filter((d) => d !== data) : [...prev, data]
    );
  };

  return (
    <AuthGuard requiredRole={UserRole.PLANNER}>
      <DashboardShell>
          {loading && (
            <div className="fixed top-0 left-0 w-full h-full flex items-center justify-center bg-lcd-bg z-50">
              <div className="text-lcd-text tracking-normal">Loading...</div>
            </div>
          )}
          <div className="flex flex-col md:flex-row justify-between items-end gap-4 mb-4">
            <h1 className="text-3xl font-bold text-lcd-text tracking-normal">Export Data</h1>
            <MatrixButton onClick={fetchAll} className={cn(fetching && "opacity-50")}>
              {fetching ? "Fetching..." : "Refresh from backend"}
            </MatrixButton>
          </div>
          {error && (
            <p className="text-red-500 text-sm mb-4 tracking-normal">{error}</p>
          )}
          <MatrixCard title="Export Configuration" className="pixel-drop-shadow">
            <div className="mb-4">
              <h2 className="text-xl text-lcd-text tracking-normal">Select Format</h2>
              <div className="flex space-x-2">
                {(["CSV", "JSON"] as Format[]).map((format) => (
                  <MatrixButton
                    key={format}
                    onClick={() => setSelectedFormat(format)}
                    className={cn(selectedFormat === format && "bg-lcd-text text-lcd-bg hover:bg-lcd-text/80")}
                  >
                    {format}
                  </MatrixButton>
                ))}
              </div>
            </div>
            <div className="mb-4">
              <h2 className="text-xl text-lcd-text tracking-normal">Select Data</h2>
              <div className="flex space-x-2">
                {(["Incidents", "Feed Statuses"] as DataSet[]).map((data) => (
                  <MatrixButton
                    key={data}
                    onClick={() => toggleData(data)}
                    className={cn(selectedData.includes(data) && "bg-lcd-text text-lcd-bg hover:bg-lcd-text/80")}
                  >
                    {data}
                  </MatrixButton>
                ))}
              </div>
            </div>
            <div className="mb-4">
              <h2 className="text-xl text-lcd-text tracking-normal">Select Time Range</h2>
              <div className="flex space-x-2">
                {(["Last 24h", "Last Week", "All"] as Range[]).map((range) => (
                  <MatrixButton
                    key={range}
                    onClick={() => setTimeRange(range)}
                    className={cn(timeRange === range && "bg-lcd-text text-lcd-bg hover:bg-lcd-text/80")}
                  >
                    {range}
                  </MatrixButton>
                ))}
              </div>
            </div>
            <MatrixButton onClick={handleExport} className={cn(rows.length === 0 && "opacity-50")}>
              Export {rows.length > 0 ? `(${rows.length} rows)` : ""}
            </MatrixButton>
            {exportedAt && (
              <p className="text-sm mt-1 text-lcd-text tracking-normal">
                Last export: {exportedAt}
              </p>
            )}
            <div className="mt-4">
              <h3 className="text-lg text-lcd-text tracking-normal">
                Data Preview {rows.length > 5 ? "(first 5 rows)" : ""}
              </h3>
              {rows.length === 0 ? (
                <p className="text-lcd-text tracking-normal font-lcd text-sm opacity-60">
                  No rows match the current selection. Refresh or widen the time range.
                </p>
              ) : (
                <pre className="text-lcd-text tracking-normal font-mono text-xs overflow-x-auto bg-black/20 p-3 mt-2">
                  {selectedFormat === "CSV"
                    ? toCSV(preview)
                    : JSON.stringify(preview, null, 2)}
                </pre>
              )}
            </div>
          </MatrixCard>
      </DashboardShell>
    </AuthGuard>
  );
};

export default ExportPage;
