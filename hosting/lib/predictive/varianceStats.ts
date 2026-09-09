// Pure forecast-vs-actual variance stats for the predictive sidebar.
// Extracted from app/dashboard/predictive/page.tsx so the math is unit-testable.
// MAPE over rows with a nonzero actual; confidence is its complement.
// Null when there is nothing to measure.

export interface ForecastRow {
  actual?: unknown;
  forecasted?: unknown;
}

export interface VarianceStats {
  mapePct: number;
  confidencePct: number;
  n: number;
}

export function computeVarianceStats(comparisonData: ForecastRow[] | null | undefined): VarianceStats | null {
  const rows = (comparisonData || []).filter(
    (r) => Number(r?.actual) > 0 && Number.isFinite(Number(r?.forecasted))
  );
  if (rows.length === 0) return null;
  const mape =
    rows.reduce(
      (acc, r) =>
        acc + Math.abs((Number(r.forecasted) - Number(r.actual)) / Number(r.actual)),
      0
    ) / rows.length;
  return { mapePct: mape * 100, confidencePct: Math.max(0, Math.min(100, 100 - mape * 100)), n: rows.length };
}
