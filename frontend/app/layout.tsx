import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AXTRACT",
  description: "Universal document ingestion engine",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full">{children}</body>
    </html>
  );
}
