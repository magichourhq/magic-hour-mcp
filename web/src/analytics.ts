type Stage = "boot" | "runtime" | "render" | "bridge" | "tool_result" | "media_preview" | "download" | "fullscreen";
type Outcome = "started" | "succeeded" | "failed" | "cancelled" | "requested";
type Reason = "uncaught_error" | "unhandled_rejection" | "connect_failed" | "transport_error" | "tool_error" | "missing_result" | "project_failed" | "missing_download" | "unsafe_download" | "load_failed" | "host_rejected" | "request_failed" | "media_aborted" | "media_network" | "media_decode" | "media_unsupported";
export type MediaType = "image" | "video" | "audio" | "media";
type Properties = { reason?: Reason; media_type?: MediaType; output_count?: number };

const endpoint = `${new URL(import.meta.env.BASE_URL).origin}/app/events`;
let eventsSent = 0;

export function captureUiEvent(stage: Stage, outcome: Outcome, properties: Properties = {}): void {
  // Bound error loops. Analytics must never interrupt the app or expose content.
  if (eventsSent >= 100) return;
  eventsSent += 1;
  try {
    void fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "text/plain" },
      body: JSON.stringify({ stage, outcome, ...properties }),
      credentials: "omit",
      referrerPolicy: "no-referrer",
      keepalive: true,
    }).catch(() => undefined);
  } catch {
    // Best effort, including sandbox restrictions and synchronous fetch errors.
  }
}
