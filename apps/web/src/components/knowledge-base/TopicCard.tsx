"use client";

import Link from "next/link";
import { FileText, FolderOpen } from "lucide-react";
import type { TopicRead } from "@/lib/knowledge-base/knowledge-base-types";

interface TopicCardProps {
  topic: TopicRead;
  subjectSlug: string;
}

export function TopicCard({ topic, subjectSlug }: TopicCardProps) {
  return (
    <Link
      href={`/knowledge-base/${subjectSlug}/${topic.slug}`}
      className="group relative flex flex-col justify-between overflow-hidden rounded-xl border border-gray-200 bg-white p-6 shadow-sm transition-all duration-200 hover:border-blue-400 hover:shadow-md"
    >
      <div>
        <div className="flex items-center justify-between">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-50 text-blue-600 transition-colors group-hover:bg-blue-600 group-hover:text-white">
            <FolderOpen className="h-5 w-5" />
          </div>
          <span className="inline-flex items-center gap-1 text-xs font-medium text-gray-500">
            <FileText className="h-3.5 w-3.5 text-gray-400" />
            {topic.document_count} {topic.document_count === 1 ? "doc" : "docs"}
          </span>
        </div>

        <h4 className="mt-4 text-base font-semibold text-gray-900 group-hover:text-blue-600">
          {topic.name}
        </h4>

        {topic.description && (
          <p className="mt-1 line-clamp-2 text-sm text-gray-600">
            {topic.description}
          </p>
        )}
      </div>

      <div className="mt-5 border-t border-gray-100 pt-3">
        <span className="text-xs font-medium text-blue-600">
          View study materials &rarr;
        </span>
      </div>
    </Link>
  );
}
