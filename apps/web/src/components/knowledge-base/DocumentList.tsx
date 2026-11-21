"use client";

import { useMemo, useState } from "react";
import { ArrowUpDown, ChevronLeft, ChevronRight } from "lucide-react";
import { DocumentRow } from "./DocumentRow";
import { KnowledgeSearch } from "./KnowledgeSearch";
import { DocumentEmptyState } from "./DocumentEmptyState";
import type { DocumentListItem } from "@/lib/knowledge-base/knowledge-base-types";
import type { UserProfile } from "@/types";

interface DocumentListProps {
  documents: DocumentListItem[];
  total: number;
  page: number;
  pageSize: number;
  loading?: boolean;
  onPageChange: (newPage: number) => void;
  currentUser: UserProfile | null;
  onPreview: (doc: DocumentListItem) => void;
  onDownload: (doc: DocumentListItem) => Promise<void>;
  onDelete: (doc: DocumentListItem) => Promise<void>;
  onUploadClick: () => void;
}

type SortField = "title" | "date" | "size" | "pages";
type SortDirection = "asc" | "desc";

export function DocumentList({
  documents,
  total,
  page,
  pageSize,
  loading = false,
  onPageChange,
  currentUser,
  onPreview,
  onDownload,
  onDelete,
  onUploadClick,
}: DocumentListProps) {
  const [search, setSearch] = useState("");
  const [sortField, setSortField] = useState<SortField>("date");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  // Client search and sort filter
  const filteredDocuments = useMemo(() => {
    let list = [...documents];

    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(
        (d) =>
          d.title.toLowerCase().includes(q) ||
          d.original_filename.toLowerCase().includes(q) ||
          (d.uploader_display_name && d.uploader_display_name.toLowerCase().includes(q))
      );
    }

    list.sort((a, b) => {
      let cmp = 0;
      switch (sortField) {
        case "title":
          cmp = a.title.localeCompare(b.title);
          break;
        case "size":
          cmp = a.file_size_bytes - b.file_size_bytes;
          break;
        case "pages":
          cmp = a.total_pages - b.total_pages;
          break;
        case "date":
        default:
          cmp = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
          break;
      }
      return sortDirection === "asc" ? cmp : -cmp;
    });

    return list;
  }, [documents, search, sortField, sortDirection]);

  const toggleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection((prev) => (prev === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDirection("asc");
    }
  };

  if (!loading && documents.length === 0) {
    return (
      <DocumentEmptyState
        title="No notes in this topic yet"
        description="Be the first student to upload lecture notes, slides, or study guides for this topic."
        onUploadClick={onUploadClick}
        uploadButtonText="Upload Note"
      />
    );
  }

  return (
    <div className="space-y-4">
      {/* Search and Sort Toolbar */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <KnowledgeSearch
          value={search}
          onChange={setSearch}
          placeholder="Filter notes by title or uploader..."
          className="w-full sm:max-w-xs"
        />

        <div className="flex items-center gap-2 text-xs text-gray-500 self-end sm:self-auto">
          <span>Sort by:</span>
          <button
            type="button"
            onClick={() => toggleSort("date")}
            className={`rounded px-2 py-1 font-medium ${
              sortField === "date" ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            Date {sortField === "date" && (sortDirection === "asc" ? "↑" : "↓")}
          </button>
          <button
            type="button"
            onClick={() => toggleSort("title")}
            className={`rounded px-2 py-1 font-medium ${
              sortField === "title" ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            Title {sortField === "title" && (sortDirection === "asc" ? "↑" : "↓")}
          </button>
          <button
            type="button"
            onClick={() => toggleSort("pages")}
            className={`rounded px-2 py-1 font-medium ${
              sortField === "pages" ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            Pages {sortField === "pages" && (sortDirection === "asc" ? "↑" : "↓")}
          </button>
        </div>
      </div>

      {/* Documents Table */}
      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50/70">
            <tr>
              <th scope="col" className="py-3 pl-4 pr-3 text-left text-xs font-medium text-gray-500 sm:pl-6">
                Document
              </th>
              <th scope="col" className="hidden px-3 py-3 text-left text-xs font-medium text-gray-500 sm:table-cell">
                Type
              </th>
              <th scope="col" className="hidden px-3 py-3 text-left text-xs font-medium text-gray-500 md:table-cell">
                Pages
              </th>
              <th scope="col" className="hidden px-3 py-3 text-left text-xs font-medium text-gray-500 lg:table-cell">
                Uploader
              </th>
              <th scope="col" className="px-3 py-3 text-left text-xs font-medium text-gray-500">
                Extraction Status
              </th>
              <th scope="col" className="relative py-3 pl-3 pr-4 text-right text-xs font-medium text-gray-500 sm:pr-6">
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 bg-white">
            {filteredDocuments.map((doc) => (
              <DocumentRow
                key={doc.id}
                document={doc}
                currentUser={currentUser}
                onPreview={onPreview}
                onDownload={onDownload}
                onDelete={onDelete}
              />
            ))}
            {filteredDocuments.length === 0 && search && (
              <tr>
                <td colSpan={6} className="py-8 text-center text-sm text-gray-500">
                  No documents match &ldquo;{search}&rdquo;.
                </td>
              </tr>
            )}
          </tbody>
        </table>

        {/* Pagination Bar */}
        {total > pageSize && (
          <div className="flex items-center justify-between border-t border-gray-200 bg-white px-4 py-3 sm:px-6">
            <div className="text-xs text-gray-500">
              Showing <span className="font-medium">{(page - 1) * pageSize + 1}</span> to{" "}
              <span className="font-medium">{Math.min(page * pageSize, total)}</span> of{" "}
              <span className="font-medium">{total}</span> documents
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => onPageChange(page - 1)}
                disabled={page <= 1}
                className="inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-40"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
                Previous
              </button>
              <span className="text-xs font-medium text-gray-700">
                Page {page} of {totalPages}
              </span>
              <button
                type="button"
                onClick={() => onPageChange(page + 1)}
                disabled={page >= totalPages}
                className="inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-40"
              >
                Next
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
