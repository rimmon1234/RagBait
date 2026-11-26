import type { Metadata } from "next";
import { Navbar } from "@/components/Navbar";
import { PresenceTracker } from "@/components/PresenceTracker";
import "./globals.css";

export const metadata: Metadata = {
  title: "RagBait — Academic AI Study Workspace",
  description: "Collaborative, citation-verified study library and multimodal RAG assistant for study groups.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
        <body className="flex min-h-screen flex-col bg-slate-50 text-slate-900 antialiased selection:bg-indigo-100 selection:text-indigo-900" suppressHydrationWarning>
          <Navbar />
          <PresenceTracker />
          <main className="flex-1">{children}</main>
        </body>
    </html>
  );
}
