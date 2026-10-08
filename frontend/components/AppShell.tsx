"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { BookOpen, FileSearch, History, LogIn, LogOut, Menu, PanelLeftClose, PanelLeftOpen, Plus, X } from "lucide-react";
import { useAuth } from "@/lib/auth-context";

export function Logo({ size = 32 }: { size?: number }) {
  return (
    <span
      className="flex shrink-0 items-center justify-center rounded-[10px] text-white"
      style={{ width: size, height: size, background: "var(--ai-gradient)" }}
      aria-hidden
    >
      <FileSearch size={size * 0.56} strokeWidth={2.2} />
    </span>
  );
}

interface NavItem {
  href: string;
  label: string;
  icon: typeof Plus;
  needsAuth?: boolean;
  match: (path: string) => boolean;
}

const NAV: NavItem[] = [
  { href: "/workspace", label: "New extraction", icon: Plus, match: (p) => p.startsWith("/workspace") },
  { href: "/rooms", label: "DealLens", icon: BookOpen, match: (p) => p.startsWith("/rooms") },
  { href: "/history", label: "Document history", icon: History, needsAuth: true, match: (p) => p.startsWith("/history") },
];

const TITLES: [(p: string) => boolean, string, string | null][] = [
  [(p) => p.startsWith("/history"), "Document history", "Library"],
  [(p) => p.startsWith("/rooms"), "DealLens", "Data Rooms"],
  [(p) => p.startsWith("/workspace"), "Extraction workspace", null],
  [() => true, "Extraction workspace", null],
];

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { user, signOut, loading, configured } = useAuth();
  const [collapsed, setCollapsed] = useState(false);
  // The mobile drawer belongs to the page it was opened on, so navigating closes it without an effect.
  const [drawerFor, setDrawerFor] = useState<string | null>(null);
  const drawer = drawerFor === pathname;
  const setDrawer = (open: boolean) => setDrawerFor(open ? pathname : null);

  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- sync a browser-only preference after hydration
      setCollapsed(localStorage.getItem("axtract.sidebar") === "collapsed");
    } catch {
      /* storage unavailable: keep the default */
    }
  }, []);

  function toggleCollapsed() {
    setCollapsed((c) => {
      try {
        localStorage.setItem("axtract.sidebar", c ? "expanded" : "collapsed");
      } catch {
        /* ignore */
      }
      return !c;
    });
  }

  // Standalone pages (Landing page at /, and Login page at /login)
  if (pathname === "/" || pathname.startsWith("/login")) return <>{children}</>;

  const [, title, crumb] = TITLES.find(([m]) => m(pathname))!;
  const items = NAV.filter((i) => !i.needsAuth || user);

  const sidebar = (mobile: boolean) => {
    const wide = mobile || !collapsed;
    return (
      <div className="flex h-full flex-col">
        <div className={`flex h-16 items-center ${wide ? "justify-between px-4" : "justify-center"}`}>
          <Link href="/" className="flex items-center gap-2.5" aria-label="AXTRACT home">
            <Logo />
            {wide && <span className="font-display text-lg font-semibold tracking-tight text-gray-900">AXTRACT</span>}
          </Link>
          {mobile && (
            <button type="button" onClick={() => setDrawer(false)} aria-label="Close menu" className="rounded-full p-2 text-gray-600 hover:bg-gray-100">
              <X size={18} />
            </button>
          )}
        </div>

        <nav aria-label="Primary" className="flex flex-1 flex-col gap-1 px-3 py-2">
          {items.map((item) => {
            const active = item.match(pathname);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                title={wide ? undefined : item.label}
                aria-current={active ? "page" : undefined}
                className={`flex items-center gap-3 rounded-full px-3.5 py-2.5 text-sm font-medium transition-colors ${
                  active ? "bg-blue-50 text-blue-800" : "text-gray-700 hover:bg-gray-100"
                } ${wide ? "" : "justify-center px-0"}`}
              >
                <Icon size={18} strokeWidth={active ? 2.4 : 2} className="shrink-0" />
                {wide && <span className="truncate">{item.label}</span>}
              </Link>
            );
          })}
        </nav>

        <div className="border-t border-gray-200 p-3">
          {configured && !loading && user && (
            <div className={`flex items-center gap-2.5 ${wide ? "" : "flex-col"}`}>
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-blue-100 text-sm font-semibold text-blue-800" aria-hidden>
                {(user.email ?? "?").charAt(0).toUpperCase()}
              </span>
              {wide && (
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-gray-900" title={user.email ?? undefined}>{user.email}</span>
                  <span className="block text-xs text-gray-500">Signed in</span>
                </span>
              )}
              <button type="button" onClick={() => signOut()} aria-label="Sign out" title="Sign out" className="rounded-full p-2 text-gray-600 hover:bg-gray-100">
                <LogOut size={17} />
              </button>
            </div>
          )}
          {configured && !loading && !user && (
            <Link href="/login" className={`flex items-center justify-center gap-2 rounded-full bg-blue-600 py-2.5 text-sm font-medium text-white hover:bg-blue-700 ${wide ? "" : "px-0"}`} title="Sign in">
              <LogIn size={16} />
              {wide && "Sign in"}
            </Link>
          )}
          {!mobile && (
            <button
              type="button"
              onClick={toggleCollapsed}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              className="mt-2 hidden w-full items-center justify-center gap-2 rounded-full py-2 text-xs text-gray-500 hover:bg-gray-100 md:flex"
            >
              {collapsed ? <PanelLeftOpen size={16} /> : <><PanelLeftClose size={16} /> Collapse</>}
            </button>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="flex min-h-screen bg-gray-50">
      <aside
        className={`sticky top-0 hidden h-screen shrink-0 border-r border-gray-200 bg-white transition-[width] duration-200 motion-reduce:transition-none md:block ${
          collapsed ? "w-[72px]" : "w-64"
        }`}
      >
        {sidebar(false)}
      </aside>

      {drawer && (
        <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true" aria-label="Menu">
          <button type="button" aria-label="Close menu" className="absolute inset-0 bg-gray-900/40" onClick={() => setDrawer(false)} />
          <div className="ax-rise absolute inset-y-0 left-0 w-72 max-w-[85vw] bg-white shadow-lift">{sidebar(true)}</div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-gray-200 bg-white/90 px-4 backdrop-blur sm:px-6">
          <button type="button" onClick={() => setDrawer(true)} aria-label="Open menu" className="rounded-full p-2 text-gray-700 hover:bg-gray-100 md:hidden">
            <Menu size={20} />
          </button>
          <div className="min-w-0">
            {crumb && <p className="text-xs text-gray-500">{crumb}</p>}
            <h1 className="truncate font-display text-base font-semibold text-gray-900 sm:text-lg">{title}</h1>
          </div>
          <div className="ml-auto flex items-center gap-2">
            {pathname.startsWith("/history") && (
              <Link href="/workspace" className="inline-flex items-center gap-1.5 rounded-full bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-card transition-colors hover:bg-blue-700">
                <Plus size={16} /> New extraction
              </Link>
            )}
          </div>
        </header>
        <div className="min-w-0 flex-1">{children}</div>
      </div>
    </div>
  );
}
