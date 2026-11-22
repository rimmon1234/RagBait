"use client";

import Link from "next/link";
import { FolderTree, Plus } from "lucide-react";
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
      <div className="rounded-xl border border-dashed border-gray-300 p-12 text-center bg-gray-50/50">
        <FolderTree className="mx-auto h-12 w-12 text-gray-400" />
        <p className="mt-3 text-base font-semibold text-gray-900">No subjects found</p>
        <p className="mt-1 text-sm text-gray-500 max-w-md mx-auto">
          Create academic subjects to organize syllabi, topics, and lecture materials.
        </p>
        <div className="mt-6 flex justify-center">
          <Link
            href="/admin/knowledge-base/subjects"
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-700"
          >
            <Plus className="h-4 w-4" />
            <span>Add Academic Subject</span>
          </Link>
        </div>
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
