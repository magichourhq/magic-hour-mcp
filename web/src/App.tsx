import { useApp } from "@modelcontextprotocol/ext-apps/react";
import { useEffect, useState, type MouseEvent, type SyntheticEvent } from "react";
import { captureUiError } from "./analytics";

type MediaType = "image" | "video" | "audio" | "media";

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? value as Record<string, unknown>
    : {};
}

function chatGptOutput(globals: unknown): unknown {
  const host = record(globals);
  const output = record(host.toolOutput);
  return Object.keys(output).length
    ? output
    : record(record(host.toolResponseMetadata).call_tool_result).structuredContent;
}

function text(value: unknown, fallback = "—"): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function optionalText(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function capitalize(value: unknown): string {
  const normalized = text(value, "media");
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function safeMediaUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "videos.magichour.ai" ? url.href : null;
  } catch {
    return null;
  }
}

function projectType(result: Record<string, unknown>, url: string | null): MediaType {
  if (["image", "video", "audio"].includes(String(result.project_type))) {
    return result.project_type as MediaType;
  }
  const path = url ? new URL(url).pathname.toLowerCase() : "";
  if (/\.(png|jpe?g|webp|gif|avif|heic|tiff?)$/.test(path)) return "image";
  if (/\.(mp3|wav|aac|flac|m4a|ogg|opus|weba?)$/.test(path)) return "audio";
  if (/\.(mp4|m4v|mov|webm)$/.test(path)) return "video";
  return "media";
}

function reportToolResult(result: unknown): void {
  const envelope = record(result);
  const project = record(envelope.structuredContent);
  // Tool and project failures are already captured by server instrumentation.
  if (envelope.isError || ["error", "canceled", "cancelled", "timeout"].includes(text(project.status).toLowerCase())) return;
  const rawUrls = Array.isArray(project.exact_download_urls) ? project.exact_download_urls : [];
  if (!Object.keys(project).length) captureUiError("result_missing");
  else if (rawUrls.some((url) => !safeMediaUrl(url))) captureUiError("result_unsafe_download");
  else if (text(project.status).toLowerCase() === "complete" && !rawUrls.length) captureUiError("result_missing_download");
}

type PlaceholderProps = {
  icon: string;
  heading: string;
  message: string;
};

function Placeholder({ icon, heading, message }: PlaceholderProps) {
  return (
    <div className="placeholder">
      <div className="placeholder-icon" aria-hidden="true">{icon}</div>
      <strong>{heading}</strong>
      <p>{message}</p>
    </div>
  );
}

type PreviewProps = {
  type: MediaType;
  url: string | null;
  name: string;
  status: string;
  message: string;
};

function Preview({ type, url, name, status, message }: PreviewProps) {
  const failed = (event: SyntheticEvent<HTMLImageElement | HTMLMediaElement>) => {
    const code = event.currentTarget instanceof HTMLMediaElement ? event.currentTarget.error?.code : undefined;
    if (code !== 1) captureUiError(code === 2 ? "media_network" : code === 3 ? "media_decode" : code === 4 ? "media_unsupported" : "media_load");
  };
  if (status !== "complete" || !url) {
    const failed = ["error", "canceled", "cancelled", "timeout"].includes(status);
    return <Placeholder icon={failed ? "!" : "…"} heading={failed ? "Project unavailable" : "Waiting for result"} message={message} />;
  }
  if (type === "image") return <img src={url} alt={name} onError={failed} />;
  if (type === "video") return <video src={url} controls playsInline preload="metadata" aria-label={name} onError={failed} />;
  if (type === "audio") {
    return (
      <div className="audio-preview">
        <div className="audio-mark" aria-hidden="true">♪</div>
        <audio src={url} controls preload="metadata" aria-label={name} onError={failed} />
      </div>
    );
  }
  return <Placeholder icon="✓" heading="Project complete" message="Use Download to open the generated media." />;
}

export default function App() {
  const [toolOutput, setToolOutput] = useState<unknown>();
  const [storedOutput, setStoredOutput] = useState<unknown>();
  const [canFullscreen, setCanFullscreen] = useState(false);
  const { app, error: connectionError } = useApp({
    appInfo: { name: "Magic Hour project result", version: "1.0.0" },
    capabilities: {},
    onAppCreated: (createdApp) => {
      createdApp.ontoolresult = (result) => {
        if (result.isError || Object.keys(record(result.structuredContent)).length) {
          setToolOutput(result.structuredContent ?? {});
        }
      };
      createdApp.onhostcontextchanged = (context) => {
        if (context.availableDisplayModes) setCanFullscreen(context.availableDisplayModes.includes("fullscreen"));
      };
      createdApp.addEventListener("toolresult", reportToolResult);
      createdApp.onerror = () => captureUiError("bridge_transport");
    },
  });

  useEffect(() => {
    const readStoredOutput = (event?: Event) => {
      const host = (window as Window & { openai?: unknown }).openai;
      const updates = record((event as CustomEvent | undefined)?.detail).globals;
      setStoredOutput(chatGptOutput({ ...record(host), ...record(updates) }));
    };
    window.addEventListener("openai:set_globals", readStoredOutput);
    readStoredOutput();
    return () => window.removeEventListener("openai:set_globals", readStoredOutput);
  }, []);

  useEffect(() => {
    const availableModes = app?.getHostContext()?.availableDisplayModes;
    if (availableModes) setCanFullscreen(availableModes.includes("fullscreen"));
  }, [app]);

  useEffect(() => {
    if (connectionError) captureUiError("bridge_connection");
  }, [connectionError]);

  const project = record(toolOutput ?? storedOutput);
  const style = record(project.style);
  const status = text(project.status, "waiting").toLowerCase();
  const rawUrls = Array.isArray(project.exact_download_urls) ? project.exact_download_urls : [];
  const urls = rawUrls.flatMap((value) => {
    const url = safeMediaUrl(value);
    return url ? [url] : [];
  });
  const downloadUrl = urls[0] ?? null;
  const type = projectType(project, downloadUrl);
  const name = text(project.name, `${capitalize(type)} project`);
  const prompt = optionalText(project.prompt) ?? optionalText(style.prompt);
  const model = optionalText(project.model) ?? optionalText(style.model);
  const tone = status === "complete" ? "success" : ["error", "canceled", "cancelled", "timeout"].includes(status) ? "danger" : "warning";
  const message = optionalText(project.message)
    ?? optionalText(project.error)
    ?? optionalText(record(project.error).message)
    ?? (tone === "danger" ? "Magic Hour could not complete this project." : "Magic Hour is preparing the result.");

  const openDownload = async (event: MouseEvent<HTMLAnchorElement>) => {
    if (!app || !downloadUrl) return;
    const capabilities = app.getHostCapabilities();
    if (!capabilities?.downloadFile && !capabilities?.openLinks) return;
    event.preventDefault();
    try {
      const result = capabilities.downloadFile
        ? await app.downloadFile({ contents: [{ type: "resource_link", uri: downloadUrl, name }] })
        : await app.openLink({ url: downloadUrl });
      if (result.isError) captureUiError("download_rejected");
    } catch {
      captureUiError("download_failed");
    }
  };

  const requestFullscreen = async () => {
    if (!app) return;
    try {
      const result = await app.requestDisplayMode({ mode: "fullscreen" });
      if (result.mode !== "fullscreen") captureUiError("fullscreen_rejected");
    } catch {
      captureUiError("fullscreen_failed");
    }
  };

  return (
    <main className="card" aria-live="polite">
      <section className="preview" aria-label="Generated media preview">
        <Preview type={type} url={downloadUrl} name={name} status={status} message={message} />
      </section>
      <section className="content">
        <span className="badge" data-tone={tone}>{capitalize(status)}</span>
        <h1>{name}</h1>
        <details className="details">
          <summary>Generation details</summary>
          <dl className="detail-list">
            {model && <div className="detail"><dt>Model</dt><dd>{model}</dd></div>}
            {prompt && <div className="detail"><dt>Prompt</dt><dd className="detail-copy">{prompt}</dd></div>}
            <div className="detail"><dt>Project ID</dt><dd>{text(project.id)}</dd></div>
            <div className="detail"><dt>Media</dt><dd>{capitalize(type)}</dd></div>
            <div className="detail"><dt>Credits</dt><dd>{project.credits_charged == null ? "—" : String(project.credits_charged)}</dd></div>
            <div className="detail"><dt>Outputs</dt><dd>{urls.length}</dd></div>
          </dl>
        </details>
        <div className="actions">
          {downloadUrl && <a className="action" href={downloadUrl} target="_blank" rel="noopener noreferrer" onClick={openDownload}>Download</a>}
          {canFullscreen && <button className="action action-secondary" type="button" onClick={requestFullscreen}>Fullscreen</button>}
        </div>
      </section>
    </main>
  );
}
