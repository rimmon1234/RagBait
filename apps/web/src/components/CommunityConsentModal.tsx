"use client";

import { useState } from "react";
import { AlertTriangle, Lock, Shield, X } from "lucide-react";

interface CommunityConsentModalProps {
  isOpen: boolean;
  modelName: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function CommunityConsentModal({
  isOpen,
  modelName,
  onConfirm,
  onCancel,
}: CommunityConsentModalProps) {
  const [isChecked, setIsChecked] = useState(false);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="consent-title"
    >
      <div className="w-full max-w-lg rounded-xl bg-white shadow-2xl overflow-hidden border border-gray-200">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-gray-100 bg-amber-50/50 px-6 py-4">
          <div className="flex items-center gap-2.5 text-amber-900">
            <Shield className="h-5 w-5 text-amber-600" />
            <h2 id="consent-title" className="text-base font-semibold">
              Community Inference Privacy Disclosure
            </h2>
          </div>
          <button
            onClick={onCancel}
            className="rounded-lg p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content */}
        <div className="space-y-4 px-6 py-5 text-sm text-gray-600">
          <div className="rounded-lg bg-blue-50/70 p-3.5 border border-blue-100 text-blue-900">
            <p className="font-medium">
              Target Model: <span className="font-mono text-blue-700">{modelName}</span>
            </p>
            <p className="mt-1 text-xs text-blue-700">
              This request will be fulfilled by an approved peer node donating local compute.
            </p>
          </div>

          <div className="space-y-2.5">
            <div className="flex items-start gap-2.5">
              <Lock className="h-4 w-4 mt-0.5 text-gray-400 shrink-0" />
              <div>
                <p className="font-medium text-gray-900">Ephemeral In-Flight Delivery</p>
                <p className="text-xs text-gray-500">
                  RagBait coordinates the job via secure Redis Streams and never writes prompts or
                  completions to PostgreSQL or permanent server storage.
                </p>
              </div>
            </div>

            <div className="flex items-start gap-2.5">
              <AlertTriangle className="h-4 w-4 mt-0.5 text-amber-500 shrink-0" />
              <div>
                <p className="font-medium text-gray-900">Execution on Contributor Hardware</p>
                <p className="text-xs text-gray-500">
                  The model executes on another student&apos;s computer via their local Ollama instance.
                  Do not submit sensitive passwords, API keys, or private identity data.
                </p>
              </div>
            </div>
          </div>

          {/* Explicit Consent Checkbox */}
          <div className="pt-2 border-t border-gray-100">
            <label className="flex items-start gap-3 cursor-pointer select-none">
              <input
                type="checkbox"
                id="community-consent-checkbox"
                checked={isChecked}
                onChange={(e) => setIsChecked(e.target.checked)}
                className="h-4 w-4 mt-0.5 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
              <span className="text-xs font-medium text-gray-800 leading-snug">
                I understand that my prompt will be processed by a community contributor node, and I
                explicitly consent to routing this request.
              </span>
            </label>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 border-t border-gray-100 bg-gray-50 px-6 py-3.5">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100"
          >
            Cancel
          </button>
          <button
            type="button"
            id="confirm-community-consent-button"
            disabled={!isChecked}
            onClick={() => {
              if (isChecked) {
                onConfirm();
              }
            }}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Agree &amp; Continue
          </button>
        </div>
      </div>
    </div>
  );
}
