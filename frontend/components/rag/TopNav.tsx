"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export default function TopNav() {
  const path = usePathname();
  const item = (href: string, label: string, active: boolean) => (
    <Link
      href={href}
      className={`rounded-full px-3 py-1.5 text-sm font-medium transition-colors ${
        active ? "bg-blue-600 text-white shadow-card" : "text-gray-600 hover:bg-gray-100"
      }`}
    >
      {label}
    </Link>
  );
  return (
    <header className="border-b border-gray-200 bg-white shadow-card">
      <div className="mx-auto flex max-w-7xl items-center gap-3 px-6 py-2.5">
        <span className="font-display text-sm font-bold tracking-tight">AXTRACT</span>
        <nav className="flex items-center gap-1">
          {item("/", "Parser", path === "/")}
          {item("/rooms", "DealLens", path.startsWith("/rooms"))}
        </nav>
        <span className="ml-auto hidden text-xs text-gray-400 sm:block">
          It doesn&apos;t just read the data room. It audits it — and every answer comes with a receipt.
        </span>
      </div>
    </header>
  );
}
