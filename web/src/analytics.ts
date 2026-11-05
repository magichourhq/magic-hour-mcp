type UiError =
  | "bridge_connection" | "bridge_transport"
  | "result_missing" | "result_missing_download" | "result_unsafe_download"
  | "media_load" | "media_network" | "media_decode" | "media_unsupported"
  | "download_rejected" | "download_failed" | "fullscreen_rejected" | "fullscreen_failed"
  | "runtime_error" | "unhandled_rejection" | "render_error";

type ResultEvent = "preview_loaded" | "playback_started" | "download_requested";
type ResultProperties = {
  event: ResultEvent;
  project_id: string;
  project_type: "image" | "video" | "audio";
  output_id: string;
};

const endpoint = `${new URL(import.meta.env.BASE_URL).origin}/app/events`;
let eventsSent = 0;
const resultsSent = new Set<string>();

export function captureUiError(error: UiError): void {
  // Bound error loops. Analytics must never interrupt the app or expose content.
  if (eventsSent >= 100) return;
  eventsSent += 1;
  sendEvent({ error });
}

export async function captureUiResult(event: ResultEvent, projectId: string, projectType: ResultProperties["project_type"], outputUrl: string): Promise<void> {
  try {
    if (resultsSent.size >= 100) return;
    // Signed queries rotate; hash the stable output path without transmitting its filename or URL.
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(new URL(outputUrl).pathname));
    const outputId = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    const key = JSON.stringify([event, projectId, projectType, outputId]);
    if (resultsSent.has(key) || resultsSent.size >= 100) return;
    resultsSent.add(key);
    sendEvent({ event, project_id: projectId, project_type: projectType, output_id: outputId });
  } catch {
    // Optional analytics must never interrupt media or downloads.
  }
}

function sendEvent(properties: { error: UiError } | ResultProperties): void {
  try {
    void fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "text/plain" },
      body: JSON.stringify(properties),
      credentials: "omit",
      referrerPolicy: "no-referrer",
      keepalive: true,
    }).catch(() => undefined);
  } catch {
    // Best effort, including sandbox restrictions and synchronous fetch errors.
  }
}
