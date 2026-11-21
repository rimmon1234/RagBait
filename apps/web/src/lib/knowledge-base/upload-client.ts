/**
 * Direct Supabase Storage Upload Client.
 *
 * Implements client-side SHA-256 calculation and one-shot signed PUT
 * upload with real-time XMLHttpRequest progress reporting.
 */

/**
 * Computes hexadecimal SHA-256 checksum of a file using standard Web Crypto API.
 */
export async function computeFileSha256(file: File | Blob): Promise<string> {
  let buffer: ArrayBuffer;
  if (typeof file.arrayBuffer === "function") {
    buffer = await file.arrayBuffer();
  } else {
    // Fallback using FileReader for test environments (e.g. JSDOM)
    buffer = await new Promise<ArrayBuffer>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as ArrayBuffer);
      reader.onerror = () => reject(reader.error || new Error("Failed to read file"));
      reader.readAsArrayBuffer(file);
    });
  }
  const hashBuffer = await crypto.subtle.digest("SHA-256", buffer);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map((b) => b.toString(16).padStart(2, "0")).join("");
}

export interface UploadProgressCallback {
  (percentage: number, loadedBytes: number, totalBytes: number): void;
}

/**
 * Uploads a file directly to private Supabase Storage using the signed PUT URL.
 * Emits progress percentages between 0 and 100 via XMLHttpRequest.
 */
export function uploadFileToStorage(
  signedUploadUrl: string,
  file: File,
  mimeType: string,
  onProgress?: UploadProgressCallback,
  signal?: AbortSignal
): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    if (signal) {
      if (signal.aborted) {
        return reject(new DOMException("Upload aborted", "AbortError"));
      }
      signal.addEventListener("abort", () => {
        xhr.abort();
        reject(new DOMException("Upload aborted", "AbortError"));
      });
    }

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        const percentage = Math.round((event.loaded / event.total) * 100);
        onProgress(percentage, event.loaded, event.total);
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(
          new Error(
            `Storage upload failed with status ${xhr.status}: ${xhr.statusText || "Server error"}`
          )
        );
      }
    };

    xhr.onerror = () => {
      reject(new Error("Storage upload network connection error"));
    };

    xhr.ontimeout = () => {
      reject(new Error("Storage upload request timed out"));
    };

    xhr.open("PUT", signedUploadUrl, true);
    xhr.setRequestHeader("Content-Type", mimeType);
    xhr.send(file);
  });
}
