"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AlertCircle, CheckCircle2, Loader2, ShieldCheck, ScanSearch, FileCheck2 } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { Logo } from "@/components/AppShell";

export default function LoginPage() {
  const { signIn, signUp, loading } = useAuth();
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setSubmitting(true);

    if (mode === "signup") {
      const { error: err, confirmed } = await signUp(email, password);
      if (err) setError(err);
      else if (confirmed) router.push("/");
      else setMessage("Check your email to confirm your account.");
    } else {
      const { error: err } = await signIn(email, password);
      if (err) setError(err);
      else router.push("/");
    }

    setSubmitting(false);
  }

  function switchMode(next: "login" | "signup") {
    setMode(next);
    setError(null);
    setMessage(null);
  }

  if (loading) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-50" aria-busy="true">
        <Loader2 className="animate-spin text-blue-600" aria-label="Loading" />
      </main>
    );
  }

  const input =
    "w-full rounded-lg border border-gray-300 bg-white px-3.5 py-2.5 text-sm transition-colors placeholder:text-gray-400 focus:border-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-100";

  return (
    <main className="grid min-h-screen bg-white lg:grid-cols-[1.05fr_1fr]">
      <section className="relative hidden flex-col justify-between overflow-hidden bg-gray-50 p-12 lg:flex">
        <div aria-hidden className="pointer-events-none absolute -bottom-32 -left-24 h-96 w-96 rounded-full opacity-[0.16] blur-3xl" style={{ background: "var(--ai-gradient)" }} />
        <div className="flex items-center gap-2.5">
          <Logo size={36} />
          <span className="font-display text-xl font-semibold tracking-tight">AXTRACT</span>
        </div>
        <div className="relative max-w-md">
          <h2 className="font-display text-4xl font-semibold leading-tight tracking-tight text-gray-900">
            Documents you can <span className="ax-ai-text">actually trust.</span>
          </h2>
          <ul className="mt-8 flex flex-col gap-5 text-sm text-gray-600">
            {[
              [ScanSearch, "Extract everything", "Text, tables, figures and equations from PDF, Office files and images."],
              [FileCheck2, "Verify independently", "Every extraction is checked against the original file, and uncertainty is shown, not hidden."],
              [ShieldCheck, "Security first", "Files are scanned before extraction and stored encrypted."],
            ].map(([Icon, title, body]) => {
              const I = Icon as typeof ShieldCheck;
              return (
                <li key={title as string} className="flex gap-3">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-white text-blue-600 shadow-card">
                    <I size={18} />
                  </span>
                  <span>
                    <span className="block font-medium text-gray-900">{title as string}</span>
                    {body as string}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
        <p className="relative text-xs text-gray-500">Universal document ingestion engine</p>
      </section>

      <section className="flex items-center justify-center p-6 sm:p-12">
        <div className="ax-rise w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <Logo />
            <span className="font-display text-lg font-semibold tracking-tight">AXTRACT</span>
          </div>
          <h1 className="font-display text-2xl font-semibold text-gray-900">
            {mode === "login" ? "Welcome back" : "Create your account"}
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            {mode === "login" ? "Sign in to save and revisit your extractions." : "Save your extractions and reopen them any time."}
          </p>

          <div role="tablist" aria-label="Sign in or sign up" className="mt-6 grid grid-cols-2 rounded-full bg-gray-100 p-1">
            {(["login", "signup"] as const).map((m) => (
              <button
                key={m}
                type="button"
                role="tab"
                aria-selected={mode === m}
                onClick={() => switchMode(m)}
                className={`rounded-full py-2 text-sm font-medium transition-all ${
                  mode === m ? "bg-white text-gray-900 shadow-card" : "text-gray-600 hover:text-gray-900"
                }`}
              >
                {m === "login" ? "Sign in" : "Sign up"}
              </button>
            ))}
          </div>

          <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-4">
            <label className="block text-sm font-medium text-gray-700">
              Email
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                required
                className={`mt-1.5 ${input}`}
              />
            </label>
            <label className="block text-sm font-medium text-gray-700">
              Password
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                required
                minLength={6}
                className={`mt-1.5 ${input}`}
              />
              {mode === "signup" && <span className="mt-1 block text-xs font-normal text-gray-500">At least 6 characters.</span>}
            </label>

            {error && (
              <p role="alert" className="flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2.5 text-sm text-red-700">
                <AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden /> {error}
              </p>
            )}
            {message && (
              <p role="status" className="flex items-start gap-2 rounded-lg bg-green-50 px-3 py-2.5 text-sm text-green-800">
                <CheckCircle2 size={16} className="mt-0.5 shrink-0" aria-hidden /> {message}
              </p>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="inline-flex items-center justify-center gap-2 rounded-full bg-blue-600 py-2.5 text-sm font-medium text-white shadow-card transition-colors hover:bg-blue-700 disabled:opacity-60"
            >
              {submitting && <Loader2 size={16} className="animate-spin" aria-hidden />}
              {submitting ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
            </button>
          </form>
        </div>
      </section>
    </main>
  );
}
