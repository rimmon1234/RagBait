import { useEffect, useRef } from "react";
import { getDocument } from "./knowledge-base-api";
import type { DocumentDetail, DocumentListItem } from "./knowledge-base-types";

const ACTIVE_EXTRACTION_STATUSES = new Set(["pending", "queued", "processing"]);
const POLL_INTERVAL_MS = 3000;

/**
 * Polls active extraction jobs every 3 seconds until terminal state or unmount.
 */
export function useDocumentStatus(
  documents: DocumentListItem[],
  onDocumentUpdate: (updated: DocumentDetail) => void
) {
  const onUpdateRef = useRef(onDocumentUpdate);
  onUpdateRef.current = onDocumentUpdate;

  const activeDocIds = documents
    .filter(
      (d) =>
        d.upload_status === "uploaded" &&
        ACTIVE_EXTRACTION_STATUSES.has(d.extraction_status)
    )
    .map((d) => d.id);

  const activeIdsKey = activeDocIds.sort().join(",");

  useEffect(() => {
    if (!activeIdsKey) {
      return;
    }

    const idsToPoll = activeIdsKey.split(",");
    let isCancelled = false;

    const poll = async () => {
      await Promise.all(
        idsToPoll.map(async (id) => {
          try {
            const updated = await getDocument(id);
            if (!isCancelled) {
              onUpdateRef.current(updated);
            }
          } catch {
            // Ignore temporary polling errors
          }
        })
      );
    };

    const intervalId = setInterval(poll, POLL_INTERVAL_MS);

    return () => {
      isCancelled = true;
      clearInterval(intervalId);
    };
  }, [activeIdsKey]);
}
