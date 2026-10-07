"use client";

import Link from "next/link";
import { useAuth } from "@/lib/auth-context";

export default function NavBar() {
  const { user, signOut, loading, configured } = useAuth();

  return (
    <nav className="border-b border-gray-200 bg-white px-6 py-2">
      <div className="mx-auto flex max-w-5xl items-center justify-between">
        <div className="flex items-center gap-6">
          <Link href="/" className="text-sm font-bold tracking-tight">
            AXTRACT
          </Link>
          {user && (
            <Link
              href="/history"
              className="text-xs text-gray-600 hover:text-gray-900"
            >
              History
            </Link>
          )}
        </div>

        {configured && (
          <div className="flex items-center gap-3 text-xs">
            {loading ? (
              <span className="text-gray-400">...</span>
            ) : user ? (
              <>
                <span className="text-gray-500">{user.email}</span>
                <button
                  type="button"
                  onClick={() => signOut()}
                  className="rounded border border-gray-300 px-2.5 py-1 text-gray-600 hover:bg-gray-50"
                >
                  Sign Out
                </button>
              </>
            ) : (
              <Link
                href="/login"
                className="rounded bg-gray-900 px-3 py-1.5 text-white hover:bg-gray-700"
              >
                Sign In
              </Link>
            )}
          </div>
        )}
      </div>
    </nav>
  );
}
