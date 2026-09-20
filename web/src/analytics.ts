type UiError =
  | "bridge_connection" | "bridge_transport"
  | "result_missing" | "result_missing_download" | "result_unsafe_download"
  | "media_load" | "media_network" | "media_decode" | "media_unsupported"
  | "download_rejected" | "download_failed" | "fullscreen_rejected" | "fullscreen_failed"
  | "runtime_error" | "unhandled_rejection" | "render_error";

const endpoint = `${new URL(import.meta.env.BASE_URL).origin}/app/events`;
let eventsSent = 0;

export function captureUiError(error: UiError): void {
  // Bound error loops. Analytics must never interrupt the app or expose content.
  if (eventsSent >= 100) return;
  eventsSent += 1;
  try {
    void fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "text/plain" },
      body: JSON.stringify({ error }),
      credentials: "omit",
      referrerPolicy: "no-referrer",
      keepalive: true,
    }).catch(() => undefined);
  } catch {
    // Best effort, including sandbox restrictions and synchronous fetch errors.
  }
}
