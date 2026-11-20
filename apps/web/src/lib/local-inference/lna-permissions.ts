import type { LoopbackPermissionState } from "./ollama-types";

/**
 * Checks whether the current window context is a secure context as defined by W3C / modern browsers.
 * Loopback Network Access (LNA) in modern Chrome requires a secure context (HTTPS) or
 * the browser's documented development exception (localhost / 127.0.0.1).
 */
export function isSecureLoopbackContext(): boolean {
  if (typeof window === "undefined") {
    return true;
  }
  if (typeof window.isSecureContext === "boolean") {
    return window.isSecureContext;
  }
  const proto = window.location.protocol;
  const host = window.location.hostname;
  return proto === "https:" || host === "localhost" || host === "127.0.0.1";
}

export interface LoopbackPermissionResult {
  state: LoopbackPermissionState;
  permissionStatus?: PermissionStatus;
}

/**
 * Queries the browser for 'loopback-network' permission state (Chrome Local Network Access).
 * Gracefully returns 'unsupported' on browsers that do not yet implement the Local Network Access
 * permission descriptor or when running in test / SSR environments.
 */
export async function queryLoopbackPermission(): Promise<LoopbackPermissionResult> {
  if (typeof navigator === "undefined" || !navigator.permissions?.query) {
    return { state: "unsupported" };
  }

  try {
    // Cast to PermissionName per W3C Local Network Access specification
    const status = await navigator.permissions.query({
      name: "loopback-network" as PermissionName,
    });

    return {
      state: status.state as LoopbackPermissionState,
      permissionStatus: status,
    };
  } catch {
    // Permission 'loopback-network' is not supported by this browser (e.g. Safari, Firefox, older Chrome, jsdom)
    return { state: "unsupported" };
  }
}
