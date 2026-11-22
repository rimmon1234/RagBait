import { createClient } from "@/lib/supabase/client";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export class ApiError extends Error {
  code: string;
  status: number;
  details?: unknown;

  constructor(code: string, message: string, status: number, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

export async function fetchWithAuth<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();

  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");

  if (session?.access_token) {
    headers.set("Authorization", `Bearer ${session.access_token}`);
  }

  const url = `${API_BASE.replace(/\/$/, "")}${endpoint.startsWith("/") ? endpoint : `/${endpoint}`}`;

  const res = await fetch(url, {
    ...options,
    headers,
  });

  if (!res.ok) {
    let errCode = "HTTP_ERROR";
    let errMsg = `Request failed with status ${res.status}`;
    let errDetails: unknown = null;

    try {
      const data = await res.json();
      if (data.error) {
        errCode = data.error.code || errCode;
        errMsg = data.error.message || errMsg;
        errDetails = data.error.details;
        if (Array.isArray(errDetails) && errDetails.length > 0) {
          const detailMsgs = errDetails
            .map((d: any) => {
              const field = Array.isArray(d.loc)
                ? d.loc.filter((p: any) => p !== "body").join(".")
                : d.loc || "";
              const msg = d.msg || JSON.stringify(d);
              return field ? `${field}: ${msg}` : msg;
            })
            .join("; ");
          errMsg = `${errMsg}: ${detailMsgs}`;
        }
      } else if (data.detail) {
        if (Array.isArray(data.detail)) {
          errMsg = data.detail
            .map((d: any) =>
              d.msg ? `${d.loc ? d.loc.slice(1).join(".") + ": " : ""}${d.msg}` : JSON.stringify(d)
            )
            .join("; ");
        } else {
          errMsg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
        }
      }
    } catch {
      // Body not JSON
    }

    throw new ApiError(errCode, errMsg, res.status, errDetails);
  }

  if (res.status === 204) {
    return {} as T;
  }

  return res.json() as Promise<T>;
}
