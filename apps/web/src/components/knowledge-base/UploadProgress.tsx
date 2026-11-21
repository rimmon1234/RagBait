"use client";

import { CheckCircle2, Clock, Loader2, ShieldCheck, UploadCloud } from "lucide-react";
import type { UploadStage } from "@/lib/knowledge-base/knowledge-base-types";

interface UploadProgressProps {
  stage: UploadStage;
  progress: number;
}

const STAGES: { stage: UploadStage; label: string }[] = [
  { stage: "hashing", label: "Computing integrity checksum" },
  { stage: "reserving", label: "Reserving upload slot" },
  { stage: "uploading", label: "Uploading file directly to storage" },
  { stage: "confirming", label: "Verifying and queuing extraction" },
  { stage: "completed", label: "Upload confirmed & queued" },
];

export function UploadProgress({ stage, progress }: UploadProgressProps) {
  if (stage === "idle" || stage === "error") {
    return null;
  }

  const getStageStatus = (targetStage: UploadStage) => {
    const order: UploadStage[] = ["hashing", "reserving", "uploading", "confirming", "completed"];
    const currentIndex = order.indexOf(stage);
    const targetIndex = order.indexOf(targetStage);

    if (stage === "completed" || currentIndex > targetIndex) {
      return "completed";
    }
    if (currentIndex === targetIndex) {
      return "active";
    }
    return "pending";
  };

  return (
    <div className="rounded-lg border border-blue-100 bg-blue-50/50 p-4">
      <div className="flex items-center justify-between text-xs font-medium text-gray-700">
        <span className="flex items-center gap-1.5">
          {stage === "completed" ? (
            <CheckCircle2 className="h-4 w-4 text-green-600" />
          ) : (
            <Loader2 className="h-4 w-4 animate-spin text-blue-600" />
          )}
          {STAGES.find((s) => s.stage === stage)?.label || "Processing upload..."}
        </span>
        <span className="font-semibold text-blue-700">{progress}%</span>
      </div>

      {/* Progress Bar */}
      <div className="mt-2.5 h-2 w-full overflow-hidden rounded-full bg-blue-200/60">
        <div
          className="h-full bg-blue-600 transition-all duration-300 ease-out"
          style={{ width: `${Math.min(100, Math.max(5, progress))}%` }}
        />
      </div>

      {/* Stage indicators */}
      <div className="mt-3 flex justify-between text-[11px] text-gray-500">
        {STAGES.map((s) => {
          const status = getStageStatus(s.stage);
          return (
            <div
              key={s.stage}
              className={`flex items-center gap-1 ${
                status === "completed"
                  ? "font-medium text-green-700"
                  : status === "active"
                    ? "font-medium text-blue-700"
                    : "text-gray-400"
              }`}
            >
              {status === "completed" ? (
                <CheckCircle2 className="h-3 w-3" />
              ) : status === "active" ? (
                <span className="h-2 w-2 rounded-full bg-blue-600 animate-pulse" />
              ) : (
                <span className="h-1.5 w-1.5 rounded-full bg-gray-300" />
              )}
              <span className="hidden sm:inline">
                {s.stage === "hashing" && "Checksum"}
                {s.stage === "reserving" && "Reserve"}
                {s.stage === "uploading" && "Upload"}
                {s.stage === "confirming" && "Verify"}
                {s.stage === "completed" && "Queued"}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
