"use client";

import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle, Loader2, UploadCloud, X } from "lucide-react";
import { useDocumentUpload } from "@/lib/knowledge-base/useDocumentUpload";
import { useKnowledgeSubjects } from "@/lib/knowledge-base/useKnowledgeSubjects";
import { useKnowledgeTopics } from "@/lib/knowledge-base/useKnowledgeTopics";
import { UploadDropzone } from "./UploadDropzone";
import { UploadProgress } from "./UploadProgress";
import type { SubjectRead, TopicRead } from "@/lib/knowledge-base/knowledge-base-types";

interface DocumentUploadModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
  initialSubjectId?: string;
  initialTopicId?: string;
  availableSubjects?: SubjectRead[];
}

export function DocumentUploadModal({
  isOpen,
  onClose,
  onSuccess,
  initialSubjectId,
  initialTopicId,
  availableSubjects,
}: DocumentUploadModalProps) {
  const { subjects: fetchedSubjects } = useKnowledgeSubjects();
  const subjects = availableSubjects || fetchedSubjects;

  const [selectedSubjectId, setSelectedSubjectId] = useState<string>(
    initialSubjectId || ""
  );
  const [selectedTopicId, setSelectedTopicId] = useState<string>(
    initialTopicId || ""
  );
  const [title, setTitle] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  const { topics, loading: topicsLoading } = useKnowledgeTopics(selectedSubjectId);
  const { stage, progress, error, upload, reset } = useDocumentUpload();

  // Keep state synchronized with props when opened
  useEffect(() => {
    if (isOpen) {
      if (initialSubjectId) setSelectedSubjectId(initialSubjectId);
      if (initialTopicId) setSelectedTopicId(initialTopicId);
      reset();
    } else {
      setSelectedFile(null);
      setTitle("");
      reset();
    }
  }, [isOpen, initialSubjectId, initialTopicId, reset]);

  // Set default subject if not provided and subjects loaded
  useEffect(() => {
    if (!selectedSubjectId && subjects.length > 0) {
      setSelectedSubjectId(subjects[0].id);
    }
  }, [selectedSubjectId, subjects]);

  // Set default topic when topics load
  useEffect(() => {
    if (!selectedTopicId && topics.length > 0) {
      setSelectedTopicId(topics[0].id);
    } else if (topics.length > 0 && !topics.some((t) => t.id === selectedTopicId)) {
      setSelectedTopicId(topics[0].id);
    }
  }, [selectedTopicId, topics]);

  const isUploading =
    stage === "hashing" ||
    stage === "reserving" ||
    stage === "uploading" ||
    stage === "confirming";

  // Handle Escape key to close modal (if not actively uploading)
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isUploading) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, isUploading, onClose]);

  if (!isOpen) return null;

  const handleFileSelect = (file: File) => {
    setSelectedFile(file);
    if (!title) {
      // Auto-populate title with friendly name (remove extension, replace dashes/underscores with space)
      const baseName = file.name.replace(/\.[^/.]+$/, "").replace(/[-_]/g, " ");
      setTitle(baseName.charAt(0).toUpperCase() + baseName.slice(1));
    }
  };

  const handleClearFile = () => {
    setSelectedFile(null);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedFile || !selectedSubjectId || !selectedTopicId) return;

    try {
      await upload(selectedFile, {
        subjectId: selectedSubjectId,
        topicId: selectedTopicId,
        title: title.trim() || selectedFile.name,
      });
      // Short delay to let user see 100% completion
      setTimeout(() => {
        onSuccess();
        onClose();
      }, 600);
    } catch {
      // Handled in useDocumentUpload state
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50 backdrop-blur-sm transition-opacity"
        onClick={() => !isUploading && onClose()}
      />

      {/* Modal Dialog */}
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="upload-modal-title"
        className="relative w-full max-w-lg rounded-xl bg-white p-6 shadow-xl transition-all"
      >
        <div className="flex items-center justify-between border-b border-gray-100 pb-4">
          <div className="flex items-center gap-2 text-gray-900">
            <UploadCloud className="h-5 w-5 text-blue-600" />
            <h2 id="upload-modal-title" className="text-lg font-semibold">Upload Study Material</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={isUploading}
            className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600 disabled:opacity-50"
            aria-label="Close modal"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          {/* Subject & Topic Selectors */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label
                htmlFor="upload-subject"
                className="block text-xs font-medium text-gray-700"
              >
                Subject
              </label>
              <select
                id="upload-subject"
                value={selectedSubjectId}
                onChange={(e) => {
                  setSelectedSubjectId(e.target.value);
                  setSelectedTopicId("");
                }}
                disabled={isUploading || Boolean(initialSubjectId)}
                className="mt-1 block w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-100"
              >
                {subjects.map((sub) => (
                  <option key={sub.id} value={sub.id}>
                    {sub.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label
                htmlFor="upload-topic"
                className="block text-xs font-medium text-gray-700"
              >
                Topic
              </label>
              <select
                id="upload-topic"
                value={selectedTopicId}
                onChange={(e) => setSelectedTopicId(e.target.value)}
                disabled={isUploading || Boolean(initialTopicId) || topicsLoading}
                className="mt-1 block w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-100"
              >
                {topicsLoading ? (
                  <option value="">Loading topics...</option>
                ) : topics.length === 0 ? (
                  <option value="">No topics available</option>
                ) : (
                  topics.map((top) => (
                    <option key={top.id} value={top.id}>
                      {top.name}
                    </option>
                  ))
                )}
              </select>
            </div>
          </div>

          {/* Document Title */}
          <div>
            <label
              htmlFor="upload-title"
              className="block text-xs font-medium text-gray-700"
            >
              Document Title
            </label>
            <input
              id="upload-title"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Unit 3 - Relational Algebra & Normalization"
              disabled={isUploading}
              required
              className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 placeholder-gray-400 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-100"
            />
          </div>

          {/* File Picker / Dropzone */}
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              File (PDF or TXT, max 50 MiB)
            </label>
            <UploadDropzone
              onFileSelect={handleFileSelect}
              selectedFile={selectedFile}
              onClear={handleClearFile}
              disabled={isUploading}
            />
          </div>

          {/* Upload Progress */}
          <UploadProgress stage={stage} progress={progress} />

          {/* Error Message */}
          {error && (
            <div className="flex items-start gap-2 rounded-lg bg-red-50 p-3 text-xs text-red-700">
              <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
              <div>
                <p className="font-semibold">Upload failed</p>
                <p className="mt-0.5">{error}</p>
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center justify-end gap-3 border-t border-gray-100 pt-4">
            <button
              type="button"
              onClick={onClose}
              disabled={isUploading}
              className="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={
                isUploading ||
                !selectedFile ||
                !selectedSubjectId ||
                !selectedTopicId ||
                !title.trim()
              }
              className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50"
            >
              {isUploading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Uploading...
                </>
              ) : (
                <>
                  <UploadCloud className="h-4 w-4" />
                  Upload & Queue
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
