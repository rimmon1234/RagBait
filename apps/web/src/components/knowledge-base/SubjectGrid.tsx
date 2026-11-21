"use client";

import { SubjectCard } from "./SubjectCard";
import type { SubjectRead } from "@/lib/knowledge-base/knowledge-base-types";

interface SubjectGridProps {
  subjects: SubjectRead[];
  loading?: boolean;
}

export function SubjectGrid({ subjects, loading }: SubjectGridProps) {
  if (loading) {
    return (
      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="flex flex-col justify-between rounded-xl border border-gray-200 bg-white p-6 shadow-sm"
          >
            <div>
              <div className="flex items-center justify-between">
                <div className="h-12 w-12 animate-pulse rounded-lg bg-gray-200" />
                <div className="h-5 w-16 animate-pulse rounded bg-gray-200" />
              </div>
              <div className="mt-4 h-6 w-3/4 animate-pulse rounded bg-gray-200" />
              <div className="mt-2 h-4 w-full animate-pulse rounded bg-gray-100" />
              <div className="mt-1 h-4 w-2/3 animate-pulse rounded bg-gray-100" />
            </div>
            <div className="mt-6 border-t border-gray-100 pt-4">
              <div className="h-4 w-24 animate-pulse rounded bg-gray-100" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (subjects.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-gray-300 p-12 text-center">
        <p className="text-base font-medium text-gray-900">No subjects found</p>
        <p className="mt-1 text-sm text-gray-500">
          Try adjusting your search query or check back later.
        </p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
      {subjects.map((subject) => (
        <SubjectCard key={subject.id} subject={subject} />
      ))}
    </div>
  );
}
