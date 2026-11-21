import { useCallback, useState } from "react";
import { confirmUpload, createUploadIntent } from "./knowledge-base-api";
import type {
  ConfirmUploadResponse,
  UploadIntentResponse,
  UploadStage,
} from "./knowledge-base-types";
import { computeFileSha256, uploadFileToStorage } from "./upload-client";

export interface UploadMetadata {
  subjectId: string;
  topicId: string;
  title: string;
}

export function useDocumentUpload() {
  const [stage, setStage] = useState<UploadStage>("idle");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ConfirmUploadResponse | null>(null);

  const reset = useCallback(() => {
    setStage("idle");
    setProgress(0);
    setError(null);
    setResult(null);
  }, []);

  const upload = useCallback(
    async (file: File, meta: UploadMetadata): Promise<ConfirmUploadResponse> => {
      reset();

      // Basic client validation
      const MAX_SIZE = 52_428_800; // 50 MiB
      if (file.size > MAX_SIZE) {
        const msg = "File exceeds the maximum allowed size of 50 MiB.";
        setError(msg);
        setStage("error");
        throw new Error(msg);
      }

      let mimeType = file.type;
      const lowerName = file.name.toLowerCase();
      if (!mimeType) {
        if (lowerName.endsWith(".pdf")) mimeType = "application/pdf";
        else if (lowerName.endsWith(".txt")) mimeType = "text/plain";
      }

      if (mimeType !== "application/pdf" && mimeType !== "text/plain") {
        const msg = "Only PDF (.pdf) and Text (.txt) files are supported.";
        setError(msg);
        setStage("error");
        throw new Error(msg);
      }

      try {
        // Step 1: Compute SHA-256 Checksum
        setStage("hashing");
        setProgress(15);
        const checksum = await computeFileSha256(file);

        // Step 2: Request Upload Reservation Intent
        setStage("reserving");
        setProgress(30);
        const intent: UploadIntentResponse = await createUploadIntent({
          subject_id: meta.subjectId,
          topic_id: meta.topicId,
          title: meta.title.trim() || file.name.replace(/\.[^/.]+$/, ""),
          original_filename: file.name,
          mime_type: mimeType,
          file_size_bytes: file.size,
          sha256_checksum: checksum,
        });

        // Step 3: Direct PUT Upload to Private Storage with progress
        setStage("uploading");
        await uploadFileToStorage(
          intent.signed_upload_url,
          file,
          mimeType,
          (pct) => {
            // Map 0-100% of storage upload to 30%-85% overall progress
            const overall = 30 + Math.round(pct * 0.55);
            setProgress(overall);
          }
        );

        // Step 4: Confirm Upload with Backend
        setStage("confirming");
        setProgress(92);
        const confirmation = await confirmUpload(intent.document_id, {
          sha256_checksum: checksum,
          file_size_bytes: file.size,
        });

        setProgress(100);
        setStage("completed");
        setResult(confirmation);
        return confirmation;
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Document upload failed.";
        setError(msg);
        setStage("error");
        throw err;
      }
    },
    [reset]
  );

  return {
    stage,
    progress,
    error,
    result,
    upload,
    reset,
  };
}
