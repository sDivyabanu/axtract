"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { rag } from "@/lib/rag";

const TABS = [
  { slug: "", label: "Data Room" },
  { slug: "ask", label: "Ask" },
  { slug: "compare", label: "Compare" },
  { slug: "packs", label: "Diligence Packs" },
  { slug: "contradictions", label: "Contradictions" },
  { slug: "seller-questions", label: "Seller Questions" },
  { slug: "maturity", label: "Maturity Wall" },
  { slug: "quarantine", label: "Quarantine" },
  { slug: "eval", label: "Eval" },
  { slug: "audit", label: "Audit" },
];

function RoomShell({ children }: { children: React.ReactNode }) {
  const { id } = useParams<{ id: string }>();
  const path = usePathname();
  const [name, setName] = useState("");
  const [mode, setMode] = useState<{ mode: string; model: string; reason: string } | null>(null);

  useEffect(() => {
    rag.getWorkspace(id).then((w) => setName(w.name)).catch(() => setName("Unknown data room"));
    const load = () =>
      rag.status().then((s) => setMode({ mode: s.mode, model: s.llm.model, reason: s.llm.reason })).catch(() => setMode(null));
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [id]);

  return (
    <div className="ax-rise mx-auto max-w-7xl px-6 pb-10">
      <div className="flex flex-wrap items-center gap-3 py-4">
        <Link href="/rooms" className="text-sm font-medium text-gray-500 hover:text-blue-600 transition-colors">← Data rooms</Link>
        <h1 className="font-display text-xl font-semibold tracking-tight">{name || "…"}</h1>
        {mode && (
          <span
            title={mode.mode === "llm" ? `Local model ${mode.model}` : mode.reason}
            className={`ml-auto rounded-full px-2.5 py-1 text-xs font-medium shadow-xs ${
              mode.mode === "llm" ? "bg-green-100 text-green-800" : "bg-amber-100 text-amber-800"
            }`}
          >
            {mode.mode === "llm" ? `LLM: ${mode.model}` : "LLM offline – extractive mode"}
          </span>
        )}
      </div>
      <nav className="mb-5 flex flex-wrap gap-1 border-b border-gray-200">
        {TABS.map((t) => {
          const href = `/rooms/${id}${t.slug ? `/${t.slug}` : ""}`;
          const active = t.slug ? path.startsWith(href) : path === href;
          return (
            <Link key={t.slug} href={href}
              className={`px-3 py-2 text-sm font-medium transition-colors ${
                active ? "border-b-2 border-blue-600 text-blue-600" : "text-gray-500 hover:text-blue-600"
              }`}>
              {t.label}
            </Link>
          );
        })}
      </nav>
      {children}
    </div>
  );
}

export default function RoomLayout({ children }: { children: React.ReactNode }) {
  // useParams/usePathname are runtime data: they must be read under Suspense (prerendering).
  return (
    <Suspense fallback={<p className="p-6 text-sm text-gray-500">Loading…</p>}>
      <RoomShell>{children}</RoomShell>
    </Suspense>
  );
}
