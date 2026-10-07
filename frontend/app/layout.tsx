import type { Metadata } from "next";
import "./globals.css";
import "katex/dist/katex.min.css";
import { Suspense } from "react";
import TopNav from "@/components/rag/TopNav";

export const metadata: Metadata = {
  title: "AXTRACT",
  description: "Universal document ingestion engine",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full">
        <Suspense fallback={null}>
          <TopNav />
        </Suspense>
        {children}
      </body>
    </html>
  );
}
