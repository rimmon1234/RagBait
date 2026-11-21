import { useEffect, useRef } from "react";
import { getDocument } from "./knowledge-base-api";
import type { DocumentDetail, DocumentListItem } from "./knowledge-base-types";

const ACTIVE_EXTRACTION_STATUSES = new Set(["pending", "queued", "processing"]);
const BASE_POLL_INTERVAL_MS = 3000;
const MAX_POLL_INTERVAL_MS = 15000;
const MAX_CONCURRENT_POLLS = 3;

/**
 * Polls active extraction jobs with bounded concurrency, exponential error backoff,
 * and visibilitychange pausing until terminal state or unmount.
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
    let isPollRunning = false;
    let consecutiveFailures = 0;
    let timerId: NodeJS.Timeout | null = null;

    // Concurrency-bounded queue processor
    const pollBatch = async () => {
      if (isCancelled || isPollRunning) return;

      // Pause polling if the browser tab/window is hidden
      if (typeof document !== "undefined" && document.visibilityState === "hidden") {
        return;
      }

      isPollRunning = true;
      let roundHadSuccess = false;

      try {
        const queue = [...idsToPoll];
        const workers = Array.from(
          { length: Math.min(MAX_CONCURRENT_POLLS, queue.length) },
          async () => {
            while (queue.length > 0 && !isCancelled) {
              const docId = queue.shift();
              if (!docId) break;
              try {
                const updated = await getDocument(docId);
                if (!isCancelled) {
                  onUpdateRef.current(updated);
                  roundHadSuccess = true;
                }
              } catch {
                // Ignore individual document fetch failures
              }
            }
          }
        );

        await Promise.all(workers);

        if (roundHadSuccess) {
          consecutiveFailures = 0;
        } else {
          consecutiveFailures++;
        }
      } catch {
        consecutiveFailures++;
      } finally {
        isPollRunning = false;
        scheduleNext();
      }
    };

    const scheduleNext = () => {
      if (isCancelled) return;
      if (timerId) clearTimeout(timerId);

      // Bounded backoff upon consecutive failures
      const backoffFactor = Math.min(consecutiveFailures, 5);
      const delay = Math.min(
        BASE_POLL_INTERVAL_MS * Math.pow(1.5, backoffFactor),
        MAX_POLL_INTERVAL_MS
      );

      timerId = setTimeout(() => {
        pollBatch();
      }, delay);
    };

    // When tab becomes visible again, trigger immediate poll
    const handleVisibilityChange = () => {
      if (typeof document !== "undefined" && document.visibilityState === "visible") {
        consecutiveFailures = 0;
        pollBatch();
      }
    };

    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", handleVisibilityChange);
    }

    // Initial schedule
    scheduleNext();

    return () => {
      isCancelled = true;
      if (timerId) clearTimeout(timerId);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", handleVisibilityChange);
      }
    };
  }, [activeIdsKey]);
}
