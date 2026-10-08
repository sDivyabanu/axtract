"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  ArrowRight,
  Check,
  CheckCircle2,
  Cpu,
  Download,
  Eye,
  FileCheck2,
  FileSearch,
  FileSpreadsheet,
  FileText,
  Layers,
  Loader2,
  Lock,
  Mail,
  ScanSearch,
  ShieldCheck,
  Sparkles,
  Zap,
} from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { Logo } from "@/components/AppShell";

export default function LandingPage() {
  const { user, signIn, signUp, signOut, loading: authLoading } = useAuth();
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleAuthSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setSubmitting(true);

    try {
      if (mode === "signup") {
        const { error: err, confirmed } = await signUp(email, password);
        if (err) {
          setError(err);
        } else if (confirmed) {
          router.push("/workspace");
        } else {
          setMessage("Check your email for the confirmation link to activate your account.");
        }
      } else {
        const { error: err } = await signIn(email, password);
        if (err) {
          setError(err);
        } else {
          router.push("/workspace");
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Authentication failed. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  function switchMode(next: "login" | "signup") {
    setMode(next);
    setError(null);
    setMessage(null);
  }

  function scrollToAuth() {
    const el = document.getElementById("auth-section");
    if (el) {
      el.scrollIntoView({ behavior: "smooth" });
      const inputEl = el.querySelector<HTMLInputElement>("input[type='email']");
      if (inputEl) inputEl.focus();
    }
  }

  const inputClass =
    "w-full rounded-xl border border-gray-300 bg-white px-4 py-2.5 text-sm transition-all placeholder:text-gray-400 focus:border-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-100";

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900 selection:bg-blue-100">
      {/* -------------------- Top Navigation -------------------- */}
      <header className="sticky top-0 z-40 border-b border-gray-200/80 bg-white/90 backdrop-blur-md">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <Logo size={34} />
            <span className="font-display text-xl font-bold tracking-tight text-gray-900">
              AXTRACT
            </span>
            <span className="hidden rounded-full bg-blue-50 px-2.5 py-0.5 text-xs font-semibold text-blue-700 sm:inline-block">
              v2.0
            </span>
          </div>

          <nav className="hidden items-center gap-8 md:flex">
            <a
              href="#capabilities"
              className="text-sm font-medium text-gray-600 transition-colors hover:text-gray-900"
            >
              Capabilities
            </a>
            <a
              href="#validation"
              className="text-sm font-medium text-gray-600 transition-colors hover:text-gray-900"
            >
              Validation Engine
            </a>
            <a
              href="#pipeline"
              className="text-sm font-medium text-gray-600 transition-colors hover:text-gray-900"
            >
              Pipeline
            </a>
            <a
              href="#security"
              className="text-sm font-medium text-gray-600 transition-colors hover:text-gray-900"
            >
              Security
            </a>
          </nav>

          <div className="flex items-center gap-3">
            {user ? (
              <div className="flex items-center gap-3">
                <span className="hidden text-xs text-gray-500 sm:inline-block">
                  Signed in as <strong className="font-medium text-gray-800">{user.email}</strong>
                </span>
                <Link
                  href="/workspace"
                  className="inline-flex items-center gap-2 rounded-full bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-card transition-colors hover:bg-blue-700"
                >
                  Go to Workspace <ArrowRight size={15} />
                </Link>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => {
                    switchMode("login");
                    scrollToAuth();
                  }}
                  className="rounded-full px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100"
                >
                  Sign in
                </button>
                <button
                  type="button"
                  onClick={() => {
                    switchMode("signup");
                    scrollToAuth();
                  }}
                  className="inline-flex items-center gap-1.5 rounded-full bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-card transition-colors hover:bg-blue-700"
                >
                  Get started <ArrowRight size={15} />
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      {/* -------------------- Hero & Auth Section -------------------- */}
      <section className="relative overflow-hidden pt-8 pb-16 lg:pt-14 lg:pb-24">
        {/* Ambient Gradient Glows */}
        <div
          aria-hidden
          className="pointer-events-none absolute -left-40 top-0 h-96 w-96 rounded-full opacity-20 blur-3xl"
          style={{ background: "var(--ai-gradient)" }}
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -right-40 top-40 h-[28rem] w-[28rem] rounded-full opacity-15 blur-3xl"
          style={{ background: "var(--ai-gradient)" }}
        />

        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="grid items-start gap-12 lg:grid-cols-12 lg:gap-8">
            {/* Left Column: Hero Banner Content */}
            <div className="ax-rise lg:col-span-7">
              <span className="inline-flex items-center gap-1.5 rounded-full border border-blue-200/80 bg-blue-50 px-3.5 py-1.5 text-xs font-semibold text-blue-800 shadow-sm">
                <Sparkles size={14} className="text-blue-600" /> Evidence-backed document intelligence
              </span>

              <h1 className="mt-5 font-display text-4xl font-semibold leading-[1.12] tracking-tight text-gray-900 sm:text-5xl lg:text-6xl">
                Turn any document into{" "}
                <span className="ax-ai-text">trusted, structured intelligence.</span>
              </h1>

              <p className="mt-5 text-base sm:text-lg leading-relaxed text-gray-600 max-w-2xl">
                Extract text, tables, figures, equations and metadata with mathematical precision —
                with independent validation and visual source-grounding built into every step.
              </p>

              {/* Banner Feature List */}
              <div className="mt-8 rounded-2xl border border-gray-200/90 bg-white/80 p-5 shadow-card backdrop-blur-sm sm:p-6">
                <p className="text-xs font-semibold uppercase tracking-wider text-gray-400">
                  Built-in Verification Standards
                </p>
                <ul className="mt-3 flex flex-col gap-3 sm:gap-2.5">
                  {[
                    "Security scan before extraction to sandbox & filter threats",
                    "Independent dual-layer check against the original file",
                    "Click any extracted block to see its exact source coordinate preview",
                  ].map((t) => (
                    <li key={t} className="flex items-start gap-2.5 text-sm text-gray-700">
                      <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-50 text-blue-600">
                        <ShieldCheck size={14} />
                      </span>
                      <span>{t}</span>
                    </li>
                  ))}
                </ul>
              </div>

              {/* Supported Formats Pill Bar */}
              <div className="mt-6 flex flex-wrap items-center gap-2 text-xs font-medium text-gray-500">
                <span className="text-gray-400">Supported Formats:</span>
                {["PDF (Vector & OCR)", "DOCX", "PPTX", "XLSX", "PNG / JPG", "ZIP Archives"].map(
                  (fmt) => (
                    <span
                      key={fmt}
                      className="rounded-md border border-gray-200 bg-white px-2 py-1 text-gray-700 shadow-xs"
                    >
                      {fmt}
                    </span>
                  )
                )}
              </div>
            </div>

            {/* Right Column: Embedded Sign In / Sign Up Card */}
            <div id="auth-section" className="ax-rise lg:col-span-5">
              <div className="relative rounded-3xl border border-gray-200/90 bg-white p-6 shadow-lift sm:p-8">
                {/* Glow accent */}
                <div
                  aria-hidden
                  className="pointer-events-none absolute -right-10 -top-10 h-40 w-40 rounded-full opacity-10 blur-2xl"
                  style={{ background: "var(--ai-gradient)" }}
                />

                {user ? (
                  <div className="flex flex-col items-center text-center py-4">
                    <div
                      className="flex h-16 w-16 items-center justify-center rounded-2xl text-white shadow-card"
                      style={{ background: "var(--ai-gradient)" }}
                    >
                      <FileSearch size={32} />
                    </div>
                    <h3 className="mt-4 font-display text-2xl font-semibold text-gray-900">
                      Welcome back!
                    </h3>
                    <p className="mt-1 text-sm text-gray-600">
                      You are signed in as <span className="font-semibold text-gray-900">{user.email}</span>
                    </p>

                    <div className="mt-6 flex w-full flex-col gap-3">
                      <Link
                        href="/workspace"
                        className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-blue-600 py-3 text-sm font-semibold text-white shadow-card transition-colors hover:bg-blue-700"
                      >
                        Open Extraction Workspace <ArrowRight size={16} />
                      </Link>
                      <Link
                        href="/history"
                        className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-gray-300 bg-white py-2.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
                      >
                        View Saved Documents
                      </Link>
                      <button
                        type="button"
                        onClick={() => signOut()}
                        className="text-xs text-gray-500 hover:text-red-600 transition-colors mt-2"
                      >
                        Sign out
                      </button>
                    </div>
                  </div>
                ) : (
                  <div>
                    <div className="flex items-center justify-between">
                      <div>
                        <h2 className="font-display text-xl font-semibold text-gray-900 sm:text-2xl">
                          {mode === "login" ? "Sign in to AXTRACT" : "Create your account"}
                        </h2>
                        <p className="mt-1 text-xs sm:text-sm text-gray-500">
                          {mode === "login"
                            ? "Access your extraction workspace and document history."
                            : "Extract documents with real-time verification and storage."}
                        </p>
                      </div>
                    </div>

                    {/* Mode Toggle */}
                    <div
                      role="tablist"
                      aria-label="Authentication mode"
                      className="mt-5 grid grid-cols-2 rounded-xl bg-gray-100 p-1"
                    >
                      <button
                        type="button"
                        role="tab"
                        aria-selected={mode === "login"}
                        onClick={() => switchMode("login")}
                        className={`rounded-lg py-2 text-xs sm:text-sm font-medium transition-all ${
                          mode === "login"
                            ? "bg-white text-gray-900 shadow-card"
                            : "text-gray-600 hover:text-gray-900"
                        }`}
                      >
                        Sign in
                      </button>
                      <button
                        type="button"
                        role="tab"
                        aria-selected={mode === "signup"}
                        onClick={() => switchMode("signup")}
                        className={`rounded-lg py-2 text-xs sm:text-sm font-medium transition-all ${
                          mode === "signup"
                            ? "bg-white text-gray-900 shadow-card"
                            : "text-gray-600 hover:text-gray-900"
                        }`}
                      >
                        Create account
                      </button>
                    </div>

                    <form onSubmit={handleAuthSubmit} className="mt-5 flex flex-col gap-4">
                      <div>
                        <label className="block text-xs font-semibold uppercase tracking-wider text-gray-600">
                          Email address
                        </label>
                        <div className="relative mt-1.5">
                          <Mail
                            size={16}
                            className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400"
                          />
                          <input
                            type="email"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            autoComplete="email"
                            placeholder="name@company.com"
                            required
                            className={`${inputClass} pl-10`}
                          />
                        </div>
                      </div>

                      <div>
                        <label className="block text-xs font-semibold uppercase tracking-wider text-gray-600">
                          Password
                        </label>
                        <div className="relative mt-1.5">
                          <Lock
                            size={16}
                            className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400"
                          />
                          <input
                            type="password"
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            autoComplete={mode === "login" ? "current-password" : "new-password"}
                            placeholder="••••••••"
                            required
                            minLength={6}
                            className={`${inputClass} pl-10`}
                          />
                        </div>
                        {mode === "signup" && (
                          <span className="mt-1 block text-xs text-gray-500">
                            Minimum 6 characters
                          </span>
                        )}
                      </div>

                      {error && (
                        <div
                          role="alert"
                          className="flex items-start gap-2 rounded-xl bg-red-50 p-3 text-xs text-red-700"
                        >
                          <AlertCircle size={15} className="mt-0.5 shrink-0" />
                          <span>{error}</span>
                        </div>
                      )}

                      {message && (
                        <div
                          role="status"
                          className="flex items-start gap-2 rounded-xl bg-green-50 p-3 text-xs text-green-800"
                        >
                          <CheckCircle2 size={15} className="mt-0.5 shrink-0" />
                          <span>{message}</span>
                        </div>
                      )}

                      <button
                        type="submit"
                        disabled={submitting || authLoading}
                        className="mt-1 inline-flex items-center justify-center gap-2 rounded-xl bg-blue-600 py-3 text-sm font-semibold text-white shadow-card transition-all hover:bg-blue-700 disabled:opacity-60"
                      >
                        {submitting && <Loader2 size={16} className="animate-spin" />}
                        {submitting
                          ? "Please wait…"
                          : mode === "login"
                          ? "Sign in & Open Workspace"
                          : "Create Free Account"}
                      </button>
                    </form>

                    <p className="mt-4 text-center text-xs text-gray-500">
                      Protected by end-to-end sandbox security & encrypted document storage.
                    </p>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* -------------------- Interactive Live Preview Showcase -------------------- */}
      <section id="validation" className="border-y border-gray-200 bg-white py-16 lg:py-20">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="mx-auto max-w-3xl text-center">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-800">
              <Eye size={14} /> Interactive Source Grounding
            </span>
            <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight text-gray-900 sm:text-4xl">
              See exact bounding boxes for every extracted element
            </h2>
            <p className="mt-3 text-base text-gray-600">
              Never trust a black-box LLM without proof. AXTRACT maps extracted tables, paragraphs, and formulas directly to coordinates on the original PDF or scanned image.
            </p>
          </div>

          <div className="mt-12 overflow-hidden rounded-2xl border border-gray-200 bg-gray-50 p-4 shadow-card lg:p-6">
            <div className="grid gap-6 lg:grid-cols-2">
              {/* Simulated Original Document View */}
              <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-xs">
                <div className="flex items-center justify-between border-b border-gray-100 pb-3">
                  <span className="text-xs font-semibold uppercase tracking-wider text-gray-500 flex items-center gap-1.5">
                    <FileText size={14} /> Original Document Preview (Page 1)
                  </span>
                  <span className="rounded bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700">
                    Source Coordinate Bounding Box
                  </span>
                </div>
                <div className="mt-4 space-y-3 font-mono text-xs text-gray-600">
                  <div className="h-4 w-3/4 rounded bg-gray-200" />
                  <div className="h-4 w-5/6 rounded bg-gray-200" />
                  <div className="rounded-lg border-2 border-blue-500 bg-blue-50/50 p-3 shadow-sm">
                    <div className="flex items-center justify-between text-blue-900 font-sans font-medium">
                      <span>Table 4.1: Quarterly Revenue Breakdown ($M)</span>
                      <span className="text-[10px] bg-blue-600 text-white px-1.5 py-0.5 rounded">bbox: [72, 140, 520, 290]</span>
                    </div>
                    <div className="mt-2 grid grid-cols-3 gap-2 text-center text-[11px] text-gray-800">
                      <div className="bg-white p-1 rounded border border-blue-200 font-semibold">Q1: $14.2M</div>
                      <div className="bg-white p-1 rounded border border-blue-200 font-semibold">Q2: $18.9M</div>
                      <div className="bg-white p-1 rounded border border-blue-200 font-semibold">Q3: $22.4M</div>
                    </div>
                  </div>
                  <div className="h-4 w-2/3 rounded bg-gray-200" />
                  <div className="h-4 w-4/5 rounded bg-gray-200" />
                </div>
              </div>

              {/* Simulated Extracted Intelligence View */}
              <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-xs">
                <div className="flex items-center justify-between border-b border-gray-100 pb-3">
                  <span className="text-xs font-semibold uppercase tracking-wider text-gray-500 flex items-center gap-1.5">
                    <FileCheck2 size={14} className="text-green-600" /> Extracted & Verified Block
                  </span>
                  <span className="inline-flex items-center gap-1 rounded-full bg-green-50 px-2.5 py-0.5 text-xs font-medium text-green-700">
                    <Check size={12} /> Verified (99.8% Match)
                  </span>
                </div>
                <div className="mt-4">
                  <div className="rounded-lg bg-gray-900 p-3 text-xs font-mono text-green-400">
                    <code>
                      {`{\n  "block_type": "table",\n  "page_index": 1,\n  "confidence": 0.998,\n  "data": [\n    {"quarter": "Q1", "revenue": 14.2},\n    {"quarter": "Q2", "revenue": 18.9},\n    {"quarter": "Q3", "revenue": 22.4}\n  ]\n}`}
                    </code>
                  </div>
                  <div className="mt-3 flex items-center justify-between text-xs text-gray-500">
                    <span>Clean Markdown, JSON, and CSV exports ready</span>
                    <Link
                      href={user ? "/workspace" : "/#auth-section"}
                      className="text-blue-600 hover:text-blue-800 font-medium inline-flex items-center gap-1"
                    >
                      Try with your files <ArrowRight size={13} />
                    </Link>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* -------------------- Core Capabilities Grid -------------------- */}
      <section id="capabilities" className="py-16 lg:py-24">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="mx-auto max-w-3xl text-center">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-800">
              <Cpu size={14} /> Full Document Spectrum
            </span>
            <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight text-gray-900 sm:text-4xl">
              Engineered for complex real-world files
            </h2>
            <p className="mt-3 text-base text-gray-600">
              From dense 10-K filings with nested tables to scanned handwritten documents and presentations.
            </p>
          </div>

          <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {[
              {
                icon: FileSpreadsheet,
                title: "Complex Table Extraction",
                desc: "Accurately extracts borderless, multi-column, and merged cell tables with row-level hierarchy.",
              },
              {
                icon: ScanSearch,
                title: "Visual OCR & Scanned Docs",
                desc: "High-resolution OCR pipeline converts low-contrast scans and receipts into structured text.",
              },
              {
                icon: Zap,
                title: "Formulas & Equations",
                desc: "Extracts mathematical notation directly into clean KaTeX/LaTeX formats with full rendering.",
              },
              {
                icon: Layers,
                title: "Batch & ZIP Archive Unpacking",
                desc: "Upload ZIP archives with up to 50 documents — unpacked client-side with decompression bomb protection.",
              },
              {
                icon: ShieldCheck,
                title: "Hallucination Defense",
                desc: "Dual-layer secondary validation guarantees that LLM generated extractions match raw bytes.",
              },
              {
                icon: Download,
                title: "Multi-Format Export",
                desc: "Instantly export extracted intelligence into JSON, Markdown, CSV, or standalone ZIP archives.",
              },
            ].map((feat) => {
              const Icon = feat.icon;
              return (
                <div
                  key={feat.title}
                  className="rounded-2xl border border-gray-200/90 bg-white p-6 shadow-card transition-all hover:shadow-lift"
                >
                  <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
                    <Icon size={22} />
                  </div>
                  <h3 className="mt-4 font-display text-lg font-semibold text-gray-900">
                    {feat.title}
                  </h3>
                  <p className="mt-2 text-sm text-gray-600 leading-relaxed">{feat.desc}</p>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* -------------------- 15-Stage Pipeline Section -------------------- */}
      <section id="pipeline" className="border-t border-gray-200 bg-white py-16 lg:py-24">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="grid items-center gap-12 lg:grid-cols-12 lg:gap-8">
            <div className="lg:col-span-5">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-800">
                <Zap size={14} /> Real-Time Event Streaming
              </span>
              <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight text-gray-900 sm:text-4xl">
                15-stage pipeline with live progress
              </h2>
              <p className="mt-4 text-base text-gray-600 leading-relaxed">
                Watch every phase as it happens via SSE event streaming. No mysterious spinning loaders — you see exactly when security scans pass, layout parsing begins, and validation concludes.
              </p>
              <div className="mt-6">
                <Link
                  href={user ? "/workspace" : "/#auth-section"}
                  className="inline-flex items-center gap-2 rounded-full bg-blue-600 px-5 py-2.5 text-sm font-medium text-white shadow-card transition-colors hover:bg-blue-700"
                >
                  Start an extraction <ArrowRight size={15} />
                </Link>
              </div>
            </div>

            <div className="lg:col-span-7">
              <div className="rounded-2xl border border-gray-200 bg-gray-50 p-6 shadow-card">
                <div className="space-y-3">
                  {[
                    { stage: "01", name: "Pre-flight Security & Sandboxing", status: "Completed", time: "12ms" },
                    { stage: "02", name: "Format Detection & Layout Decomposition", status: "Completed", time: "85ms" },
                    { stage: "03", name: "High-Resolution OCR & Text Recognition", status: "Completed", time: "140ms" },
                    { stage: "04", name: "Table & Equation Entity Structuring", status: "Completed", time: "210ms" },
                    { stage: "05", name: "Secondary Independent Evidence Verification", status: "Completed", time: "95ms" },
                  ].map((s) => (
                    <div
                      key={s.stage}
                      className="flex items-center justify-between rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-xs"
                    >
                      <div className="flex items-center gap-3">
                        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-green-50 text-green-700 text-xs font-bold">
                          <Check size={14} />
                        </span>
                        <div>
                          <span className="text-xs font-mono text-gray-400 mr-2">{s.stage}</span>
                          <span className="text-sm font-medium text-gray-800">{s.name}</span>
                        </div>
                      </div>
                      <span className="text-xs font-mono text-gray-500 bg-gray-100 px-2 py-0.5 rounded">
                        {s.time}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* -------------------- Security & Compliance -------------------- */}
      <section id="security" className="py-16 lg:py-24 bg-gray-50 border-t border-gray-200">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="mx-auto max-w-3xl text-center">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-800">
              <ShieldCheck size={14} /> Enterprise-Grade Privacy
            </span>
            <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight text-gray-900 sm:text-4xl">
              Zero compromises on file security
            </h2>
            <p className="mt-3 text-base text-gray-600">
              Documents are processed in isolated sandboxes and encrypted in your dedicated database.
            </p>
          </div>

          <div className="mt-12 grid gap-6 sm:grid-cols-3">
            <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-card">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
                <Lock size={20} />
              </div>
              <h3 className="mt-4 font-display text-base font-semibold text-gray-900">
                Encrypted Storage
              </h3>
              <p className="mt-2 text-xs sm:text-sm text-gray-600 leading-relaxed">
                Uploaded files and parsed JSON structures are stored with AES-256 encryption at rest.
              </p>
            </div>

            <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-card">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
                <ShieldCheck size={20} />
              </div>
              <h3 className="mt-4 font-display text-base font-semibold text-gray-900">
                Pre-Execution Threat Scan
              </h3>
              <p className="mt-2 text-xs sm:text-sm text-gray-600 leading-relaxed">
                Embedded macros, nested archive bombs, and malicious payloads are filtered prior to parsing.
              </p>
            </div>

            <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-card">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
                <Eye size={20} />
              </div>
              <h3 className="mt-4 font-display text-base font-semibold text-gray-900">
                Isolated Client Unzipping
              </h3>
              <p className="mt-2 text-xs sm:text-sm text-gray-600 leading-relaxed">
                ZIP files are unpacked entirely in your browser memory — raw archives never reach the server.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* -------------------- Bottom CTA Banner -------------------- */}
      <section className="bg-white border-t border-gray-200 py-16">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="relative overflow-hidden rounded-3xl bg-gray-900 px-6 py-12 text-white shadow-lift sm:px-12 sm:py-16">
            <div
              aria-hidden
              className="pointer-events-none absolute -right-24 -top-24 h-80 w-80 rounded-full opacity-30 blur-3xl"
              style={{ background: "var(--ai-gradient)" }}
            />
            <div className="relative max-w-2xl">
              <h2 className="font-display text-3xl font-semibold tracking-tight sm:text-4xl text-white">
                Ready to extract trusted intelligence?
              </h2>
              <p className="mt-4 text-base text-gray-300">
                Join now and turn your PDFs, reports, and spreadsheets into verified structured data in seconds.
              </p>
              <div className="mt-8 flex flex-wrap gap-4">
                <Link
                  href={user ? "/workspace" : "/#auth-section"}
                  className="inline-flex items-center gap-2 rounded-full bg-blue-600 px-6 py-3 text-sm font-semibold text-white shadow-card transition-colors hover:bg-blue-500"
                >
                  {user ? "Open Workspace" : "Get started for free"} <ArrowRight size={16} />
                </Link>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* -------------------- Footer -------------------- */}
      <footer className="border-t border-gray-200 bg-gray-50 py-10 text-xs text-gray-500">
        <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-4 px-4 sm:flex-row sm:px-6 lg:px-8">
          <div className="flex items-center gap-2.5">
            <Logo size={24} />
            <span className="font-display text-sm font-bold tracking-tight text-gray-900">
              AXTRACT
            </span>
            <span>· Universal Document Ingestion Engine</span>
          </div>
          <p>© 2026 AXTRACT. Evidence-backed document intelligence.</p>
        </div>
      </footer>
    </div>
  );
}
