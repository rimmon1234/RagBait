"use client";

import {
  AlertTriangle,
  Archive,
  BookOpen,
  CheckCircle2,
  Clock,
  FileText,
  FolderTree,
  Loader2,
} from "lucide-react";
import type { AdminOverviewMetrics } from "@/lib/knowledge-base/knowledge-base-admin-types";

interface Props {
  metrics: AdminOverviewMetrics;
}

export function AdminOverviewCards({ metrics }: Props) {
  const cards = [
    {
      title: "Active Subjects",
      value: metrics.active_subjects_count,
      subtext: `${metrics.total_subjects_count} total subjects`,
      icon: BookOpen,
      iconColor: "text-blue-600",
      bgColor: "bg-blue-50",
    },
    {
      title: "Active Topics",
      value: metrics.active_topics_count,
      subtext: `${metrics.total_topics_count} total topics`,
      icon: FolderTree,
      iconColor: "text-indigo-600",
      bgColor: "bg-indigo-50",
    },
    {
      title: "Uploaded Documents",
      value: metrics.total_documents_count,
      subtext: `${metrics.archived_documents_count} archived`,
      icon: FileText,
      iconColor: "text-green-600",
      bgColor: "bg-green-50",
    },
    {
      title: "Extraction Queue",
      value: metrics.queued_jobs_count + metrics.processing_jobs_count,
      subtext: `${metrics.queued_jobs_count} queued, ${metrics.processing_jobs_count} running`,
      icon: Clock,
      iconColor: "text-amber-600",
      bgColor: "bg-amber-50",
    },
    {
      title: "Failed Jobs",
      value: metrics.failed_jobs_count,
      subtext: "Eligible for admin retry",
      icon: AlertTriangle,
      iconColor: "text-red-600",
      bgColor: "bg-red-50",
    },
    {
      title: "Completed Extractions",
      value: metrics.completed_jobs_count,
      subtext: "Processed pages available",
      icon: CheckCircle2,
      iconColor: "text-emerald-600",
      bgColor: "bg-emerald-50",
    },
  ];

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {cards.map((card) => {
        const Icon = card.icon;
        return (
          <div
            key={card.title}
            className="flex items-center gap-4 rounded-xl border border-gray-200 bg-white p-5 shadow-sm"
          >
            <div className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-xl ${card.bgColor} ${card.iconColor}`}>
              <Icon className="h-6 w-6" />
            </div>
            <div>
              <p className="text-xs font-medium text-gray-500">{card.title}</p>
              <h3 className="text-2xl font-bold text-gray-900">{card.value}</h3>
              <p className="text-xs text-gray-400">{card.subtext}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}
