"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import type { AuthChangeEvent, Session, User } from "@supabase/supabase-js";
import { createClient, isSupabaseConfigured } from "./supabase";

interface AuthState {
  user: User | null;
  session: Session | null;
  loading: boolean;
  configured: boolean;
  signUp: (email: string, password: string) => Promise<{ error: string | null; confirmed?: boolean }>;
  signIn: (email: string, password: string) => Promise<{ error: string | null }>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

function friendlyAuthError(error: { message?: string; code?: string }): string {
  const msg = error.message ?? "";
  const code = error.code ?? "";
  const lower = msg.toLowerCase();
  if (code === "over_email_send_rate_limit" || lower.includes("rate limit")) {
    return "Too many verification emails sent — the free tier allows only a few per hour. Wait a bit and retry, or disable 'Confirm email' in the Supabase dashboard (Authentication → Sign In / Providers) to skip email verification entirely.";
  }
  if (code === "user_already_exists" || lower.includes("already registered")) {
    return "An account with this email already exists. Try signing in instead.";
  }
  if (code === "invalid_credentials" || lower.includes("invalid login credentials")) {
    return "Incorrect email or password.";
  }
  if (lower.includes("not confirmed")) {
    return "Please confirm your email first — check your inbox for the verification link.";
  }
  if (code === "email_address_invalid" || (lower.includes("invalid") && lower.includes("email"))) {
    return "Please enter a valid email address.";
  }
  if (lower.includes("password")) {
    return msg;
  }
  return msg || "Something went wrong. Please try again.";
}

const noopAuth: AuthState = {
  user: null,
  session: null,
  loading: false,
  configured: false,
  signUp: async () => ({ error: "Supabase not configured" }),
  signIn: async () => ({ error: "Supabase not configured" }),
  signOut: async () => {},
};

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(isSupabaseConfigured);

  useEffect(() => {
    const supabase = createClient();
    if (!supabase) return;

    supabase.auth.getSession().then(({ data: { session: s } }: { data: { session: Session | null } }) => {
      setSession(s);
      setUser(s?.user ?? null);
      setLoading(false);
    });

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event: AuthChangeEvent, s: Session | null) => {
      setSession(s);
      setUser(s?.user ?? null);
      setLoading(false);
    });

    return () => subscription.unsubscribe();
  }, []);

  if (!isSupabaseConfigured) {
    return (
      <AuthContext value={noopAuth}>
        {children}
      </AuthContext>
    );
  }

  async function signUp(email: string, password: string) {
    const supabase = createClient();
    if (!supabase) return { error: "Supabase not configured" };
    const { data, error } = await supabase.auth.signUp({ email, password });
    if (error) return { error: friendlyAuthError(error) };
    return { error: null, confirmed: !!data.session };
  }

  async function signIn(email: string, password: string) {
    const supabase = createClient();
    if (!supabase) return { error: "Supabase not configured" };
    const { error } = await supabase.auth.signInWithPassword({
      email,
      password,
    });
    if (error) return { error: friendlyAuthError(error) };
    return { error: null };
  }

  async function signOut() {
    const supabase = createClient();
    if (!supabase) return;
    await supabase.auth.signOut();
  }

  return (
    <AuthContext value={{ user, session, loading, configured: true, signUp, signIn, signOut }}>
      {children}
    </AuthContext>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
