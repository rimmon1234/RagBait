import type { Metadata } from "next";
import { Navbar } from "@/components/Navbar";
import { PresenceTracker } from "@/components/PresenceTracker";
import "./globals.css";

export const metadata: Metadata = {
  title: "RagBait — Collaborative RAG Platform",
  description: "Collaborative educational platform for study groups and classes.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="flex min-h-screen flex-col bg-gray-50 text-gray-900 antialiased">
        <Navbar />
        <PresenceTracker />
        <main className="flex-1">{children}</main>
      </body>
    </html>
  );
}
