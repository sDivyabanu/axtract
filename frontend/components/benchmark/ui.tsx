"use client";

import { ApiError } from "@/lib/api";
import type { ScoreKind } from "@/lib/benchmark";
import { friendlyError, scoreBadge } from "@/lib/benchmark-utils";

export const card = "rounded-xl border border-gray-200 bg-white p-4 shadow-card sm:p-5";
export const btn = "inline-flex items-center justify-center gap-1.5 rounded-full border border-gray-300 px-4 py-2 text-sm text-gray-800 hover:bg-gray-50 disabled:opacity-50";
export const btnPrimary = "inline-flex items-center justify-center gap-1.5 rounded-full bg-blue-600 px-5 py-2.5 text-sm font-medium text-white shadow-card hover:bg-blue-700 disabled:opacity-50";
export const input = "rounded-full border border-gray-300 bg-white px-4 py-2 text-sm focus:border-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-100";
export const th = "py-2 pr-4 text-xs font-medium uppercase tracking-wide text-gray-500";

const TONES: Record<string, string> = {
  amber: "border-amber-200 bg-amber-50 text-amber-900",
  green: "border-green-200 bg-green-50 text-green-800",
  red: "border-red-200 bg-red-50 text-red-800",
  gray: "border-gray-200 bg-gray-50 text-gray-700",
  blue: "border-blue-200 bg-blue-50 text-blue-800",
};

export function ScoreBadge({ kind }: { kind: ScoreKind }) {
  const b = scoreBadge(kind);
  return (
    <span title={b.detail} className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-medium ${TONES[b.tone]}`}>
      {b.label}
    </span>
  );
}

/** Always shown next to a score so a sample or diagnostic number can never pass for an official one. */
export function ScoreNotice({ kind }: { kind: ScoreKind }) {
  const b = scoreBadge(kind);
  if (b.tone === "green") return null;
  return (
    <p role="note" className={`rounded-lg border px-3 py-2 text-xs ${TONES[b.tone]}`}>
      <strong>{b.label}.</strong> {b.detail}
    </p>
  );
}

export function ErrorBanner({ error, onDismiss }: { error: string | null; onDismiss?: () => void }) {
  if (!error) return null;
  return (
    <div role="alert" className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      <span className="min-w-0 flex-1 break-words">{error}</span>
      {onDismiss && (
        <button type="button" onClick={onDismiss} className="shrink-0 font-medium underline">
          Dismiss
        </button>
      )}
    </div>
  );
}

export function errorText(e: unknown, fallback = "Something went wrong."): string {
  if (e instanceof ApiError) return friendlyError(e.code, e.message);
  return e instanceof Error ? e.message : fallback;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="rounded-lg border border-dashed border-gray-300 p-6 text-center text-sm text-gray-500">{children}</p>;
}
