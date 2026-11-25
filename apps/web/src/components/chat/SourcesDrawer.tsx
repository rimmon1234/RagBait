"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  BookOpen,
  ExternalLink,
  FileText,
  Loader2,
  RefreshCw,
  ShieldAlert,
  X,
} from "lucide-react";
import { getMessageSources } from "@/lib/chat-client";
import type { MessageSourceRead, StreamSourceSummary } from "@/types/chat";

export interface SourcesDrawerProps {
  isOpen: boolean;
  messageId: string | null;
  subjectName?: string;
  focusedCitationKey?: string | null;
  isProvisional?: boolean;
  provisionalSources?: StreamSourceSummary[];
  onClose: () => void;
}

type SourceTab = "cited" | "considered";

export function SourcesDrawer({
  isOpen,
  messageId,
  subjectName,
  focusedCitationKey,
  isProvisional = false,
  provisionalSources,
  onClose,
}: SourcesDrawerProps) {
  const [sources, setSources] = useState<MessageSourceRead[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<SourceTab>("cited");

  const reqCounterRef = useRef(0);
  const closeBtnRef = useRef<HTMLButtonElement>(null);
  const cardRefs = useRef<Record<string, HTMLElement | null>>({});

  const loadSources = useCallback(async () => {
    if (!isOpen || !messageId || isProvisional) {
      return;
    }

    const reqId = ++reqCounterRef.current;
    setLoading(true);
    setError(null);
    // Clear prior sources immediately so stale excerpts from another message/revoked item never show
    setSources([]);

    try {
      const resp = await getMessageSources(messageId);
      if (reqCounterRef.current !== reqId) return;
      setSources(resp.sources || []);
    } catch (err) {
      if (reqCounterRef.current !== reqId) return;
      setSources([]);
      setError(err instanceof Error ? err.message : "Failed to load message sources.");
    } finally {
      if (reqCounterRef.current === reqId) {
        setLoading(false);
      }
    }
  }, [isOpen, isProvisional, messageId]);

  useEffect(() => {
    if (!isOpen || !messageId || isProvisional) {
      setSources([]);
      setError(null);
      return;
    }
    void loadSources();
  }, [isOpen, isProvisional, loadSources, messageId]);

  // Focus close button on open and close on Escape
  useEffect(() => {
    if (!isOpen) return;
    closeBtnRef.current?.focus();
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  const citedSources = useMemo(
    () => sources.filter((s) => s.referenced_in_answer),
    [sources]
  );
  const consideredSources = useMemo(
    () => sources.filter((s) => s.included_in_context),
    [sources]
  );

  // Switch to the tab containing focusedCitationKey and scroll/focus it when sources load
  useEffect(() => {
    if (!isOpen || !focusedCitationKey || sources.length === 0) return;
    const inCited = citedSources.some((s) => s.citation_key === focusedCitationKey);
    const inConsidered = consideredSources.some((s) => s.citation_key === focusedCitationKey);

    if (inCited) {
      setActiveTab("cited");
    } else if (inConsidered) {
      setActiveTab("considered");
    }

    const card = cardRefs.current[focusedCitationKey];
    if (card) {
      card.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
    }
  }, [citedSources, consideredSources, focusedCitationKey, isOpen, sources.length]);

  // Default to "considered" if the assistant made 0 inline citations yet backend recorded included context
  useEffect(() => {
    if (!focusedCitationKey && citedSources.length === 0 && consideredSources.length > 0) {
      setActiveTab("considered");
    } else if (!focusedCitationKey && citedSources.length > 0) {
      setActiveTab("cited");
    }
  }, [citedSources.length, consideredSources.length, focusedCitationKey]);

  if (!isOpen) return null;

  const displayedSources = activeTab === "cited" ? citedSources : consideredSources;

  return (
    <div className="fixed inset-0 z-50 overflow-hidden">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/40 backdrop-blur-[1px] transition-opacity"
        onClick={onClose}
        aria-hidden="true"
      />

      <div className="fixed inset-y-0 right-0 flex w-full max-w-full sm:w-[400px] md:w-[430px]">
        <aside
          role="dialog"
          aria-modal="true"
          aria-labelledby="sources-drawer-title"
          className="flex h-full w-full flex-col bg-white shadow-2xl border-l border-gray-200"
        >
          {/* Header */}
          <div className="flex items-start justify-between gap-3 border-b border-gray-200 px-5 py-4">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <BookOpen className="h-4 w-4 text-blue-600 shrink-0" />
                <h2 id="sources-drawer-title" className="text-base font-bold text-gray-900">
                  Sources &amp; Context
                </h2>
              </div>
              {subjectName && (
                <p className="mt-0.5 text-xs text-gray-500 truncate">
                  Subject scope: <span className="font-medium text-gray-700">{subjectName}</span>
                </p>
              )}
            </div>

            <div className="flex items-center gap-1.5">
              {!isProvisional && messageId && (
                <button
                  type="button"
                  onClick={() => void loadSources()}
                  disabled={loading}
                  className="rounded-lg p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
                  title="Revalidate source availability"
                  aria-label="Refresh sources"
                >
                  <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                </button>
              )}
              <button
                ref={closeBtnRef}
                type="button"
                onClick={onClose}
                className="rounded-lg p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
                aria-label="Close sources panel"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
          </div>

          {/* Authoritative Stage Tabs (only for finalized messages) */}
          {!isProvisional && (
            <div className="border-b border-gray-200 bg-gray-50 px-5 py-2.5">
              <div className="flex rounded-lg bg-gray-200/70 p-1" role="tablist">
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeTab === "cited"}
                  onClick={() => setActiveTab("cited")}
                  className={`flex-1 rounded-md px-2.5 py-1.5 text-xs font-semibold transition-colors ${
                    activeTab === "cited"
                      ? "bg-white text-gray-900 shadow-sm"
                      : "text-gray-600 hover:text-gray-900"
                  }`}
                >
                  Cited in Answer ({citedSources.length})
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeTab === "considered"}
                  onClick={() => setActiveTab("considered")}
                  className={`flex-1 rounded-md px-2.5 py-1.5 text-xs font-semibold transition-colors ${
                    activeTab === "considered"
                      ? "bg-white text-gray-900 shadow-sm"
                      : "text-gray-600 hover:text-gray-900"
                  }`}
                >
                  All Considered ({consideredSources.length})
                </button>
              </div>
            </div>
          )}

          {/* Body */}
          <div className="flex-1 overflow-y-auto p-5 space-y-4">
            {isProvisional ? (
              <div className="space-y-3">
                <div className="rounded-xl border border-amber-200 bg-amber-50 p-3.5 text-xs text-amber-900">
                  <p className="font-semibold">Generation in progress</p>
                  <p className="mt-1 text-amber-800 leading-relaxed">
                    Final cited sources and revalidated excerpts will appear here once generation completes.
                  </p>
                </div>
                {provisionalSources && provisionalSources.length > 0 && (
                  <div className="space-y-2">
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
                      Identified Candidate Context ({provisionalSources.length})
                    </p>
                    {provisionalSources.map((src) => (
                      <div
                        key={src.citation_key}
                        className="rounded-xl border border-gray-200 bg-gray-50/70 p-3 text-xs"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="inline-flex items-center rounded-md bg-gray-200 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-gray-800">
                            [{src.citation_key}]
                          </span>
                          <span className="text-gray-500">
                            {src.page_number_start === src.page_number_end
                              ? `Page ${src.page_number_start}`
                              : `Pages ${src.page_number_start}–${src.page_number_end}`}
                          </span>
                        </div>
                        <p className="mt-1.5 font-medium text-gray-900">{src.document_title}</p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : loading ? (
              <div className="flex h-48 flex-col items-center justify-center gap-3 text-sm text-gray-500">
                <Loader2 className="h-7 w-7 animate-spin text-blue-600" />
                <span>Revalidating source visibility...</span>
              </div>
            ) : error ? (
              <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-xs text-red-800 space-y-2">
                <div className="flex items-start gap-2">
                  <AlertCircle className="h-4 w-4 text-red-600 shrink-0 mt-0.5" />
                  <p className="leading-relaxed">{error}</p>
                </div>
                <button
                  type="button"
                  onClick={() => void loadSources()}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-red-300 bg-white px-2.5 py-1 text-xs font-semibold text-red-700 hover:bg-red-100"
                >
                  <RefreshCw className="h-3 w-3" />
                  Retry
                </button>
              </div>
            ) : displayedSources.length === 0 ? (
              <div className="py-12 text-center">
                <FileText className="mx-auto h-9 w-9 text-gray-300" />
                <p className="mt-2 text-sm font-medium text-gray-700">
                  {activeTab === "cited"
                    ? "No citations referenced in this response"
                    : "No source passages recorded for this response"}
                </p>
                <p className="mt-1 text-xs text-gray-500">
                  {activeTab === "cited" && consideredSources.length > 0
                    ? "Switch to All Considered to view retrieved evidence included in context."
                    : "This response did not rely on retrieved study passages."}
                </p>
              </div>
            ) : (
              displayedSources.map((src) => {
                const isFocused = focusedCitationKey === src.citation_key;
                const pageLabel =
                  src.page_number_start === src.page_number_end
                    ? `Page ${src.page_number_start}`
                    : `Pages ${src.page_number_start}–${src.page_number_end}`;

                return (
                  <article
                    key={src.citation_key}
                    ref={(el) => {
                      cardRefs.current[src.citation_key] = el;
                    }}
                    data-testid={`source-card-${src.citation_key}`}
                    className={`rounded-xl border p-4 transition-all ${
                      isFocused
                        ? "border-blue-500 bg-blue-50/40 ring-2 ring-blue-200"
                        : "border-gray-200 bg-white shadow-sm"
                    }`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="inline-flex items-center rounded-md bg-blue-100 px-2 py-0.5 font-mono text-xs font-bold text-blue-800">
                          [{src.citation_key}]
                        </span>
                        <span className="rounded bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-700">
                          {pageLabel}
                        </span>
                      </div>

                      {!src.is_available ? (
                        <span className="inline-flex items-center gap-1 rounded-md bg-amber-50 border border-amber-200 px-2 py-0.5 text-[11px] font-medium text-amber-800">
                          <ShieldAlert className="h-3 w-3" />
                          Unavailable
                        </span>
                      ) : src.referenced_in_answer ? (
                        <span className="rounded-md bg-emerald-50 border border-emerald-200 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
                          Cited
                        </span>
                      ) : (
                        <span className="rounded-md bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-600">
                          Considered
                        </span>
                      )}
                    </div>

                    <h3 className="mt-2.5 text-sm font-semibold text-gray-900 break-words">
                      {src.document_title}
                    </h3>

                    {src.is_available ? (
                      <>
                        <div className="mt-2.5 rounded-lg border border-gray-200 bg-gray-50 p-3 text-xs leading-relaxed text-gray-800 whitespace-pre-wrap break-words max-h-56 overflow-y-auto">
                          {src.excerpt}
                        </div>

                        {src.document_viewer_url && (
                          <div className="mt-3 flex justify-end">
                            <Link
                              href={src.document_viewer_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
                            >
                              <span>Open at Page {src.page_number_start}</span>
                              <ExternalLink className="h-3.5 w-3.5" />
                            </Link>
                          </div>
                        )}
                      </>
                    ) : (
                      <div className="mt-2.5 rounded-lg border border-amber-200 bg-amber-50/70 p-3 text-xs text-amber-900 leading-relaxed">
                        {src.excerpt}
                      </div>
                    )}
                  </article>
                );
              })
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
