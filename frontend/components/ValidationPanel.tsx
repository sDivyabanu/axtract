"use client";

import { useMemo, useState } from "react";
import type {
  IssueSeverity,
  ValidationIssue,
  ValidationRecovery,
  ValidationReport,
  ValidationStatus,
} from "@/lib/types";

export interface ValidationActions {
  busy: boolean;
  error: string | null;
  onEscalate: () => Promise<void>;
  onPromote: (recoveryIds: string[], reason: string) => Promise<void>;
}

interface ValidationPanelProps {
  validation: ValidationReport;
  canShowSource: boolean;
  activeIssueId: string | null;
  onSelectIssue: (issue: ValidationIssue) => void;
  actions?: ValidationActions;
}

const STATUS: Record<ValidationStatus, { label: string; badge: string; box: string; dot: string }> = {
  verified: { label: "VERIFIED", badge: "bg-green-100 text-green-800 border-green-300", box: "border-green-200 bg-green-50/40", dot: "bg-green-500" },
  recovered: { label: "RECOVERED", badge: "bg-blue-100 text-blue-800 border-blue-300", box: "border-blue-200 bg-blue-50/40", dot: "bg-blue-500" },
  review_required: { label: "REVIEW REQUIRED", badge: "bg-amber-100 text-amber-800 border-amber-300", box: "border-amber-200 bg-amber-50/40", dot: "bg-amber-500" },
  failed: { label: "FAILED", badge: "bg-red-100 text-red-800 border-red-300", box: "border-red-200 bg-red-50/40", dot: "bg-red-500" },
  not_verifiable: { label: "NOT VERIFIABLE", badge: "bg-gray-100 text-gray-700 border-gray-300", box: "border-gray-200 bg-gray-50/60", dot: "bg-gray-400" },
};

const SEVERITY: Record<IssueSeverity, { label: string; cls: string }> = {
  critical: { label: "Critical", cls: "bg-red-100 text-red-800" },
  high: { label: "High", cls: "bg-red-50 text-red-700" },
  medium: { label: "Medium", cls: "bg-amber-50 text-amber-700" },
  low: { label: "Low", cls: "bg-gray-100 text-gray-600" },
  info: { label: "Note", cls: "bg-gray-50 text-gray-500" },
};
const BLOCKING: IssueSeverity[] = ["critical", "high", "medium"];

const EVIDENCE_LABEL: Record<string, string> = {
  deterministic: "read from the file",
  model_based: "second engine",
  heuristic: "heuristic",
  unavailable: "no evidence",
};

function pct(v: number | null | undefined): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

function unitLabel(issue: ValidationIssue): string {
  const u = issue.locator.unit;
  if (u.type === "document") return "document";
  return `${u.type} ${u.label ?? u.index}`;
}

function hasLocation(issue: ValidationIssue): boolean {
  const l = issue.locator;
  return Boolean(l.block_ids.length || l.preview || l.page);
}

export default function ValidationPanel({
  validation,
  canShowSource,
  activeIssueId,
  onSelectIssue,
  actions,
}: ValidationPanelProps) {
  const [open, setOpen] = useState(validation.status !== "verified");
  const [showNotes, setShowNotes] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const s = validation.summary;
  const style = STATUS[validation.status] ?? STATUS.not_verifiable;

  const { blocking, notes } = useMemo(() => {
    const order: IssueSeverity[] = ["critical", "high", "medium", "low", "info"];
    const sorted = [...validation.issues].sort((a, b) => order.indexOf(a.severity) - order.indexOf(b.severity));
    return {
      blocking: sorted.filter((i) => BLOCKING.includes(i.severity)),
      notes: sorted.filter((i) => !BLOCKING.includes(i.severity)),
    };
  }, [validation.issues]);

  const unverifiable = validation.units.filter((u) => u.status === "not_verifiable");
  const candidates = validation.recoveries.filter((r) => r.decision === "candidate_only");
  const supported = candidates.filter((r) => r.comparison.some((c) => c.metric === "verdict" && c.value === "candidate_supported"));
  const counts: { key: ValidationStatus; n: number }[] = [
    { key: "verified", n: s.verified },
    { key: "recovered", n: s.recovered },
    { key: "review_required", n: s.review_required },
    { key: "failed", n: s.failed },
    { key: "not_verifiable", n: s.not_verifiable },
  ];

  return (
    <div className={`rounded-lg border p-4 ${style.box}`} data-testid="validation-panel">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">AXTRACT Verify</h2>
          <p className="text-xs text-gray-500">
            Independent check of this extraction against the original file. A validation result, not an accuracy score.
          </p>
        </div>
        <span className={`rounded-full border px-3 py-1 text-xs font-bold tracking-wide ${style.badge}`} data-testid="validation-status">
          {style.label}
        </span>
      </div>

      <p className="mt-2 text-sm text-gray-700">{validation.status_reason}</p>

      {validation.failure && (
        <div className="mt-2 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-700" role="alert">
          Validation could not complete (stage “{validation.failure.stage}”: {validation.failure.error_type}). The
          extraction below is unaffected.
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-2 text-xs">
        {counts.map(({ key, n }) => (
          <span key={key} className="inline-flex items-center gap-1.5 rounded border border-gray-200 bg-white px-2 py-1">
            <span className={`h-2 w-2 rounded-full ${STATUS[key].dot}`} />
            <strong>{n}</strong> {STATUS[key].label.toLowerCase()}
          </span>
        ))}
        <span className="inline-flex items-center gap-1 rounded border border-gray-200 bg-white px-2 py-1 text-gray-600" title={s.definitions?.evidence_coverage}>
          Evidence coverage <strong>{pct(s.evidence_coverage)}</strong>
        </span>
        <span className="inline-flex items-center gap-1 rounded border border-gray-200 bg-white px-2 py-1 text-gray-600" title={s.definitions?.agreement_rate}>
          Agreement <strong>{pct(s.agreement_rate)}</strong> ({s.checks_agreed}/{s.checks_decided})
        </span>
      </div>

      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="mt-3 text-xs font-medium text-gray-600 underline-offset-2 hover:underline"
        aria-expanded={open}
      >
        {open ? "Hide details" : `Show details (${blocking.length} to review, ${notes.length} notes)`}
      </button>

      {open && (
        <div className="mt-3 flex flex-col gap-4">
          {blocking.length > 0 && (
            <section aria-label="Issues to review">
              <h3 className="mb-1 text-sm font-semibold">Issues to review ({blocking.length})</h3>
              <ul className="flex flex-col gap-1.5">
                {blocking.map((issue) => (
                  <IssueRow
                    key={issue.id}
                    issue={issue}
                    active={issue.id === activeIssueId}
                    expanded={expanded === issue.id}
                    canShowSource={canShowSource}
                    onToggle={() => setExpanded(expanded === issue.id ? null : issue.id)}
                    onSelect={() => onSelectIssue(issue)}
                  />
                ))}
              </ul>
            </section>
          )}

          {unverifiable.length > 0 && (
            <section aria-label="Not verifiable">
              <h3 className="mb-1 text-sm font-semibold">Could not be verified ({unverifiable.length})</h3>
              <ul className="list-disc pl-5 text-xs text-gray-600">
                {unverifiable.slice(0, 6).map((u) => (
                  <li key={`${u.unit.type}-${u.unit.index}`}>
                    <strong>
                      {u.unit.type} {u.unit.label ?? u.unit.index}
                    </strong>
                    : {u.status_reasons[0] ?? "no independent evidence"}
                  </li>
                ))}
                {unverifiable.length > 6 && <li>…and {unverifiable.length - 6} more</li>}
              </ul>
            </section>
          )}

          {notes.length > 0 && (
            <section aria-label="Notes">
              <button type="button" onClick={() => setShowNotes((v) => !v)} className="text-xs text-gray-600 underline-offset-2 hover:underline">
                {showNotes ? "Hide notes" : `Notes: what was not compared or is only informational (${notes.length})`}
              </button>
              {showNotes && (
                <ul className="mt-1 flex flex-col gap-1.5">
                  {notes.map((issue) => (
                    <IssueRow
                      key={issue.id}
                      issue={issue}
                      active={issue.id === activeIssueId}
                      expanded={expanded === issue.id}
                      canShowSource={canShowSource}
                      onToggle={() => setExpanded(expanded === issue.id ? null : issue.id)}
                      onSelect={() => onSelectIssue(issue)}
                    />
                  ))}
                </ul>
              )}
            </section>
          )}

          {validation.recoveries.length > 0 && <Recoveries recoveries={validation.recoveries} />}

          {actions && (blocking.length > 0 || supported.length > 0) && (
            <section aria-label="Secondary validation" className="rounded border border-gray-200 bg-white p-3">
              <h3 className="mb-1 text-sm font-semibold">Secondary validation</h3>
              <p className="mb-2 text-xs text-gray-500">
                Re-reads only the flagged units with other readers. Results are candidates: nothing in the extraction changes
                until you accept one, and the original output is always kept.
              </p>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={actions.busy}
                  onClick={() => void actions.onEscalate()}
                  className="rounded border border-gray-300 px-3 py-1.5 text-xs hover:bg-gray-50 disabled:opacity-50"
                >
                  {actions.busy ? "Working…" : "Re-check flagged units"}
                </button>
                {supported.length > 0 && (
                  <button
                    type="button"
                    disabled={actions.busy}
                    onClick={() => {
                      if (window.confirm(`Accept ${supported.length} recovered reading(s) that match the independent source? The original blocks are kept and marked as superseded.`)) {
                        void actions.onPromote(supported.map((r) => r.id), "Accepted: candidate matches the independent source evidence");
                      }
                    }}
                    className="rounded bg-gray-900 px-3 py-1.5 text-xs text-white hover:bg-gray-700 disabled:opacity-50"
                  >
                    Accept {supported.length} supported recovery{supported.length === 1 ? "" : "ies"}
                  </button>
                )}
              </div>
              {actions.error && <p className="mt-2 text-xs text-red-600">{actions.error}</p>}
            </section>
          )}

          <p className="text-[11px] text-gray-400">
            Checked with {validation.providers.filter((p) => p.available).map((p) => p.name).join(", ") || "no independent reader"} in{" "}
            {Math.round(validation.timings_ms.total ?? 0)} ms.
          </p>
        </div>
      )}
    </div>
  );
}

function IssueRow({
  issue,
  active,
  expanded,
  canShowSource,
  onToggle,
  onSelect,
}: {
  issue: ValidationIssue;
  active: boolean;
  expanded: boolean;
  canShowSource: boolean;
  onToggle: () => void;
  onSelect: () => void;
}) {
  const sev = SEVERITY[issue.severity];
  const ev = issue.evidence[0];
  const located = hasLocation(issue);
  return (
    <li className={`rounded border bg-white ${active ? "border-gray-900 ring-1 ring-gray-900" : "border-gray-200"}`}>
      <div className="flex items-start gap-2 p-2">
        <button
          type="button"
          onClick={onSelect}
          disabled={!located}
          className="flex min-w-0 flex-1 items-start gap-2 text-left disabled:cursor-default"
          title={located ? (canShowSource ? "Open this location in the original document" : "Select the related block") : "This issue has no source location"}
        >
          <span className={`mt-0.5 flex-shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${sev.cls}`}>{sev.label}</span>
          <span className="min-w-0">
            <span className="block text-sm text-gray-800">{issue.message}</span>
            <span className="block text-[11px] text-gray-500">
              <code>{issue.code}</code> · {unitLabel(issue)} · {issue.layer.replace("_", " ")}
              {ev && ` · ${EVIDENCE_LABEL[ev.kind] ?? ev.kind} (${ev.engine.split(":")[0]})`}
              {located ? (canShowSource ? " · view source →" : " · select block →") : " · no source location"}
            </span>
          </span>
        </button>
        {ev && (
          <button type="button" onClick={onToggle} className="flex-shrink-0 text-[11px] text-gray-500 hover:text-gray-800" aria-expanded={expanded}>
            {expanded ? "Hide" : "Evidence"}
          </button>
        )}
      </div>
      {expanded && ev && (
        <div className="grid gap-2 border-t border-gray-100 p-2 text-xs sm:grid-cols-2">
          <div>
            <div className="mb-0.5 font-semibold text-gray-600">Original document shows</div>
            <pre className="whitespace-pre-wrap rounded bg-gray-50 p-2 text-gray-800">{ev.source ?? "—"}</pre>
          </div>
          <div>
            <div className="mb-0.5 font-semibold text-gray-600">AXTRACT extracted</div>
            <pre className="whitespace-pre-wrap rounded bg-gray-50 p-2 text-gray-800">{ev.extracted ?? "—"}</pre>
          </div>
        </div>
      )}
    </li>
  );
}

function Recoveries({ recoveries }: { recoveries: ValidationRecovery[] }) {
  return (
    <section aria-label="Recovery audit trail">
      <h3 className="mb-1 text-sm font-semibold">Recovery audit trail ({recoveries.length})</h3>
      <ul className="flex flex-col gap-1.5">
        {recoveries.map((r) => {
          const verdict = r.comparison.find((c) => c.metric === "verdict")?.value;
          return (
            <li key={r.id} className="rounded border border-gray-200 bg-white p-2 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${r.decision === "promoted" ? "bg-blue-100 text-blue-800" : "bg-gray-100 text-gray-600"}`}>
                  {r.decision.replace("_", " ")}
                </span>
                <span>
                  {r.unit.type} {r.unit.label ?? r.unit.index} · {r.provider}
                  {verdict ? ` · ${String(verdict).replace(/_/g, " ")}` : ""}
                </span>
              </div>
              <p className="mt-1 text-gray-500">{r.reason}</p>
              {r.decision === "promoted" && (
                <p className="mt-1 text-gray-500">
                  Accepted ({r.decided_by}). Original blocks kept and marked superseded: {r.original.block_ids.slice(0, 4).join(", ")}
                  {r.original.block_ids.length > 4 ? "…" : ""}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
