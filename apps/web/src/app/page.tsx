import Link from "next/link";
import {
  ArrowRight,
  BookOpen,
  FileSearch,
  KeyRound,
  Lock,
  MessageSquare,
  ShieldCheck,
  Sparkles,
  Users,
} from "lucide-react";

export default function HomePage() {
  return (
        <div className="relative overflow-hidden">
          {/* Subtle decorative ambient blobs */}
          <div
            aria-hidden="true"
            className="pointer-events-none absolute -top-28 left-1/2 -z-10 h-96 w-[56rem] -translate-x-1/2 rounded-full bg-gradient-to-r from-blue-200/35 via-indigo-200/35 to-cyan-200/35 blur-3xl"
          />

        <div className="mx-auto max-w-7xl px-4 py-14 sm:px-6 sm:py-20 lg:px-8">
          {/* Hero */}
          <div className="mx-auto max-w-3xl text-center">
            <div className="inline-flex items-center gap-2 rounded-full border border-indigo-200/80 bg-white/80 px-3.5 py-1 text-xs font-semibold text-indigo-700 shadow-sm backdrop-blur-sm">
              <Sparkles className="h-3.5 w-3.5 text-indigo-600" />
              <span>Multimodal Study Library • Diagram &amp; Citation-Verified RAG</span>
            </div>

                  <h1 className="mt-6 text-4xl font-extrabold tracking-tight text-slate-900 sm:text-6xl sm:leading-[1.08]">
                    Study Smarter with{" "}
                    <span className="bg-gradient-to-r from-blue-600 via-indigo-600 to-cyan-500 bg-clip-text text-transparent">
                      RagBait
                    </span>
                  </h1>

                  <p className="mx-auto mt-5 max-w-2xl text-base text-slate-600 sm:text-lg leading-relaxed">
                    A collaborative academic knowledge base and AI study assistant built for college cohorts—turning
                    lecture slides, flowcharts, and equations into page-cited, verifiable answers.
                  </p>

                  <div className="mt-9 flex flex-wrap items-center justify-center gap-3.5">
                    <Link href="/knowledge-base" className="btn-primary-gradient px-6 py-3 text-base">
                      <BookOpen className="h-5 w-5" />
                      Explore Knowledge Base
                      <ArrowRight className="h-4 w-4" />
                    </Link>
                    <Link
                      href="/chat"
                      className="inline-flex items-center gap-2 rounded-xl border border-indigo-200 bg-indigo-50/80 px-5 py-3 text-sm font-semibold text-indigo-700 shadow-sm transition-all hover:bg-indigo-100/80"
                    >
                      <MessageSquare className="h-4 w-4" />
                      Open AI Study Chat
                    </Link>
                    <Link
                      href="/login"
                      className="rounded-xl border border-slate-200 bg-white px-5 py-3 text-sm font-semibold text-slate-700 shadow-sm transition-colors hover:bg-slate-50"
                    >
                      Sign In
                    </Link>
                    <Link
                      href="/register"
                      className="rounded-xl border border-transparent bg-slate-100/90 px-5 py-3 text-sm font-semibold text-slate-700 transition-colors hover:bg-slate-200/80"
                    >
                      Create Account
                    </Link>
                  </div>
                </div>

                    {/* Highlight strip */}
                    <div className="mt-14 grid grid-cols-1 gap-4 rounded-2xl border border-slate-200/80 bg-white/75 p-5 shadow-sm backdrop-blur-md sm:grid-cols-3">
                      <div className="flex items-center gap-3 px-2 py-1">
                        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
                          <FileSearch className="h-5 w-5" />
                        </div>
                        <div>
                          <div className="text-sm font-bold text-slate-900">Page-Accurate Grounding</div>
                          <div className="text-xs text-slate-500">Every claim links back to physical slide pages &amp; OCR/diagrams</div>
                        </div>
                      </div>
                      <div className="flex items-center gap-3 px-2 py-1 sm:border-x sm:border-slate-200/70 sm:px-5">
                        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-indigo-50 text-indigo-600">
                          <KeyRound className="h-5 w-5" />
                        </div>
                        <div>
                          <div className="text-sm font-bold text-slate-900">AES-256-GCM Key Vault</div>
                          <div className="text-xs text-slate-500">Zero-knowledge encryption with anti-duplicate key enforcement</div>
                        </div>
                      </div>
                      <div className="flex items-center gap-3 px-2 py-1">
                        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-emerald-50 text-emerald-600">
                          <ShieldCheck className="h-5 w-5" />
                        </div>
                        <div>
                          <div className="text-sm font-bold text-slate-900">Sandboxed PDF Pipeline</div>
                          <div className="text-xs text-slate-500">Networkless, read-only container parsing for study uploads</div>
                        </div>
                      </div>
                    </div>

                        {/* Feature Cards */}
                        <div className="mt-10 grid grid-cols-1 gap-6 md:grid-cols-3">
                          <div className="card-elevated group p-6 hover:-translate-y-0.5 hover:border-blue-300/80">
                            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 text-white shadow-sm shadow-blue-600/20">
                              <Lock className="h-5 w-5" />
                            </div>
                            <h2 className="mt-5 text-lg font-bold text-slate-900">Strict Authorization &amp; Vault</h2>
                            <p className="mt-2 text-sm text-slate-600 leading-relaxed">
                              Row-Level Security (RLS), isolated vault DB roles, and cross-account fingerprint locks prevent credential sharing abuse and bypasses.
                            </p>
                          </div>

                          <div className="card-elevated group p-6 hover:-translate-y-0.5 hover:border-indigo-300/80">
                            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 text-white shadow-sm shadow-indigo-600/20">
                              <Users className="h-5 w-5" />
                            </div>
                            <h2 className="mt-5 text-lg font-bold text-slate-900">Community Free-Tier Pool</h2>
                            <p className="mt-2 text-sm text-slate-600 leading-relaxed">
                              Contributors pool verified free-tier keys with explicit consent to power cohort-wide AI study chat, or connect local Ollama privately.
                            </p>
                          </div>

                          <div className="card-elevated group p-6 hover:-translate-y-0.5 hover:border-cyan-300/80">
                            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-cyan-500 to-blue-600 text-white shadow-sm shadow-cyan-600/20">
                              <BookOpen className="h-5 w-5" />
                            </div>
                            <h2 className="mt-5 text-lg font-bold text-slate-900">Multimodal Hybrid RAG</h2>
                            <p className="mt-2 text-sm text-slate-600 leading-relaxed">
                              Combines dense vector search, full-text search, conditional OCR, and vision diagram descriptions with strict source-provenance badges.
                            </p>
                          </div>
                        </div>
                      </div>
                    </div>
                    );
}
