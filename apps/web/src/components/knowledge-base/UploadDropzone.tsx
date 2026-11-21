"use client";

import { useCallback, useRef, useState } from "react";
import { AlertCircle, FileText, UploadCloud, X } from "lucide-react";

interface UploadDropzoneProps {
  onFileSelect: (file: File) => void;
  selectedFile: File | null;
  onClear: () => void;
  disabled?: boolean;
}

const MAX_FILE_SIZE = 52_428_800; // 50 MiB
const ALLOWED_EXTENSIONS = [".pdf", ".txt"];
const ALLOWED_MIME_TYPES = ["application/pdf", "text/plain"];

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function UploadDropzone({
  onFileSelect,
  selectedFile,
  onClear,
  disabled = false,
}: UploadDropzoneProps) {
  const [isDragOver, setIsDragOver] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const validateAndSelect = useCallback(
    (file: File) => {
      setValidationError(null);

      // Validate size
      if (file.size > MAX_FILE_SIZE) {
        setValidationError(
          `File size (${formatFileSize(file.size)}) exceeds the maximum allowed limit of 50 MB.`
        );
        return;
      }

      // Validate type / extension
      const lowerName = file.name.toLowerCase();
      const hasValidExt = ALLOWED_EXTENSIONS.some((ext) => lowerName.endsWith(ext));
      const hasValidMime = ALLOWED_MIME_TYPES.includes(file.type);

      if (!hasValidExt && !hasValidMime) {
        setValidationError("Only PDF (.pdf) and plain text (.txt) files are supported.");
        return;
      }

      onFileSelect(file);
    },
    [onFileSelect]
  );

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    if (disabled) return;
    setIsDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    if (disabled) return;

    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      validateAndSelect(e.dataTransfer.files[0]);
    }
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      validateAndSelect(e.target.files[0]);
    }
  };

  if (selectedFile) {
    const isPdf = selectedFile.name.toLowerCase().endsWith(".pdf");
    return (
      <div className="rounded-lg border border-gray-200 bg-gray-50 p-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-100 text-blue-600">
              <FileText className="h-5 w-5" />
            </div>
            <div>
              <p className="max-w-xs truncate text-sm font-medium text-gray-900 sm:max-w-md">
                {selectedFile.name}
              </p>
              <p className="text-xs text-gray-500">
                {formatFileSize(selectedFile.size)} &bull; {isPdf ? "PDF Document" : "Plain Text"}
              </p>
            </div>
          </div>
          {!disabled && (
            <button
              type="button"
              onClick={onClear}
              className="rounded p-1 text-gray-400 hover:bg-gray-200 hover:text-gray-600"
              aria-label="Remove selected file"
            >
              <X className="h-5 w-5" />
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div>
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => !disabled && fileInputRef.current?.click()}
        className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed p-6 text-center transition-colors ${
          isDragOver
            ? "border-blue-500 bg-blue-50/50"
            : "border-gray-300 hover:border-gray-400 bg-gray-50/50"
        } ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.txt,application/pdf,text/plain"
          onChange={handleFileInputChange}
          className="hidden"
          disabled={disabled}
        />
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-blue-50 text-blue-600">
          <UploadCloud className="h-6 w-6" />
        </div>
        <p className="mt-3 text-sm font-medium text-gray-900">
          Click to browse or drag and drop your study note
        </p>
        <p className="mt-1 text-xs text-gray-500">
          PDF or TXT up to 50 MiB (scanned and searchable)
        </p>
      </div>

      {validationError && (
        <div className="mt-2 flex items-center gap-1.5 text-xs text-red-600">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>{validationError}</span>
        </div>
      )}
    </div>
  );
}
