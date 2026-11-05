import { useApp } from "@modelcontextprotocol/ext-apps/react";
import { useEffect, useState, type MouseEvent, type SyntheticEvent } from "react";
import { captureUiError, captureUiResult } from "./analytics";

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
  const path = url ? new URL(url).pathname.toLowerCase() : "";
  if (/\.(png|jpe?g|webp|gif|avif|heic|tiff?)$/.test(path)) return "image";
  if (/\.(mp3|wav|aac|flac|m4a|ogg|opus|weba?)$/.test(path)) return "audio";
  if (/\.(mp4|m4v|mov|webm)$/.test(path)) return "video";
  if (["image", "video", "audio"].includes(String(result.project_type))) {
    return result.project_type as MediaType;
  }
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
  projectId: string | null;
  type: MediaType;
  url: string | null;
  name: string;
  status: string;
  message: string;
};

function Preview({ projectId, type, url, name, status, message }: PreviewProps) {
  const [loadFailed, setLoadFailed] = useState(false);
  const loaded = () => {
    if (projectId && url && type !== "media") void captureUiResult("preview_loaded", projectId, type, url);
  };
  const playing = () => {
    if (projectId && url && type !== "media") void captureUiResult("playback_started", projectId, type, url);
  };
  const failed = (event: SyntheticEvent<HTMLImageElement | HTMLMediaElement>) => {
    const code = event.currentTarget instanceof HTMLMediaElement ? event.currentTarget.error?.code : undefined;
    if (code !== 1) {
      captureUiError(code === 2 ? "media_network" : code === 3 ? "media_decode" : code === 4 ? "media_unsupported" : "media_load");
      setLoadFailed(true);
    }
  };
  if (status !== "complete" || !url) {
    const failed = ["error", "canceled", "cancelled"].includes(status);
    const heading = status === "timeout" ? "Waiting paused" : failed ? "Project unavailable" : "Waiting for result";
    return <Placeholder icon={failed ? "!" : "…"} heading={heading} message={message} />;
  }
  if (loadFailed) return <Placeholder icon="!" heading="Preview unavailable" message="Try Download, or ask to refresh this result's link." />;
  if (type === "image") return <img src={url} alt={name} onLoad={loaded} onError={failed} />;
  if (type === "video") return <video src={url} controls playsInline preload="metadata" aria-label={name} onLoadedData={loaded} onPlaying={playing} onError={failed} />;
  if (type === "audio") {
    return (
      <div className="audio-preview">
        <div className="audio-mark" aria-hidden="true">♪</div>
        <audio src={url} controls preload="metadata" aria-label={name} onLoadedData={loaded} onPlaying={playing} onError={failed} />
      </div>
    );
  }
  return <Placeholder icon="✓" heading="Project complete" message="Use Download to open the generated media." />;
}

export default function App() {
  const [toolOutput, setToolOutput] = useState<unknown>();
  const [selectedOutput, setSelectedOutput] = useState(0);
  const [canFullscreen, setCanFullscreen] = useState(false);
  const [openingDraft, setOpeningDraft] = useState(false);
  const [draftFeedback, setDraftFeedback] = useState<string | null>(null);
  const [checkingProject, setCheckingProject] = useState<string | null>(null);
  const [statusFeedback, setStatusFeedback] = useState<{ id: string; message: string } | null>(null);
  const { app, error: connectionError } = useApp({
    appInfo: { name: "Magic Hour project result", version: "1.0.0" },
    capabilities: {},
    onAppCreated: (createdApp) => {
      createdApp.ontoolresult = (result) => {
        if (result.isError || Object.keys(record(result.structuredContent)).length) {
          setToolOutput(result.structuredContent ?? {});
          setSelectedOutput(0);
          setDraftFeedback(null);
          setStatusFeedback(null);
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
    const readHostOutput = (event?: Event) => {
      const host = (window as Window & { openai?: unknown }).openai;
      const updates = record((event as CustomEvent | undefined)?.detail).globals;
      if (event && !("toolOutput" in record(updates)) && !("toolResponseMetadata" in record(updates))) return;
      // A metadata-only notification must not reuse an older host.toolOutput.
      const output = chatGptOutput(event ? updates : host);
      if (!event && !Object.keys(record(output)).length) return;
      setToolOutput(output);
      setSelectedOutput(0);
      setDraftFeedback(null);
      setStatusFeedback(null);
    };
    window.addEventListener("openai:set_globals", readHostOutput);
    readHostOutput();
    return () => window.removeEventListener("openai:set_globals", readHostOutput);
  }, []);

  useEffect(() => {
    const availableModes = app?.getHostContext()?.availableDisplayModes;
    if (availableModes) setCanFullscreen(availableModes.includes("fullscreen"));
  }, [app]);

  useEffect(() => {
    if (connectionError) captureUiError("bridge_connection");
  }, [connectionError]);

  const project = record(toolOutput);
  const style = record(project.style);
  const status = text(project.status, "waiting").toLowerCase();
  const rawUrls = Array.isArray(project.exact_download_urls) ? project.exact_download_urls : [];
  const urls = rawUrls.flatMap((value) => {
    const url = safeMediaUrl(value);
    return url ? [url] : [];
  });
  const downloadUrl = urls[selectedOutput] ?? null;
  const projectId = optionalText(project.id);
  const type = projectType(project, downloadUrl);
  const hostCapabilities = app?.getHostCapabilities();
  const canOpenImageDraft = Boolean(status === "complete" && project.project_type === "image" && type === "image" && projectId && downloadUrl
    && hostCapabilities?.message?.text && hostCapabilities.experimental?.["openai/message"]);
  const canCheckStatus = Boolean(projectId && ["image", "video", "audio"].includes(String(project.project_type))
    && !["complete", "error", "canceled", "cancelled"].includes(status) && hostCapabilities?.serverTools);
  const name = text(project.name, `${capitalize(type)} project`);
  const prompt = optionalText(project.prompt) ?? optionalText(style.prompt);
  const model = optionalText(project.model) ?? optionalText(style.model);
  const tone = status === "complete" ? "success" : ["error", "canceled", "cancelled"].includes(status) ? "danger" : "warning";
  const message = optionalText(project.message)
    ?? optionalText(project.error)
    ?? optionalText(record(project.error).message)
    ?? (tone === "danger" ? "Magic Hour could not complete this project." : "Magic Hour is preparing the result.");

  const openDownload = async (event: MouseEvent<HTMLAnchorElement>) => {
    if (projectId && downloadUrl && type !== "media") void captureUiResult("download_requested", projectId, type, downloadUrl);
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

  const checkStatus = async () => {
    if (!app || !canCheckStatus || !projectId || checkingProject === projectId) return;
    const id = projectId;
    const projectType = project.project_type;
    setCheckingProject(id);
    setStatusFeedback(null);
    try {
      const result = await app.callServerTool({
        name: `wait_for_${projectType}_project`,
        arguments: { id, timeout_seconds: 5, include_inline_downloads: false, max_inline_downloads: 0 },
      });
      const updated = record(result.structuredContent);
      if (result.isError || updated.id !== id || updated.project_type !== projectType) {
        setStatusFeedback({ id, message: "Could not check this project's status. Try again." });
        return;
      }
      // A late status response must not replace a different project opened meanwhile.
      setToolOutput((current: unknown) => {
        const latest = record(current);
        return latest.id === id && !["complete", "error", "canceled", "cancelled"].includes(String(latest.status)) ? updated : current;
      });
    } catch {
      setStatusFeedback({ id, message: "Could not check this project's status. Try again." });
    } finally {
      setCheckingProject((current) => current === id ? null : current);
    }
  };

  const selectOutput = (index: number) => {
    setSelectedOutput(index);
    setDraftFeedback(null);
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

  const openImageDraft = async (action: "edit" | "animate") => {
    if (!app || !canOpenImageDraft || !projectId || !downloadUrl || openingDraft) return;
    const outputNumber = rawUrls.findIndex((value) => safeMediaUrl(value) === downloadUrl) + 1;
    const draft = {
      role: "user" as const,
      content: [
        { type: "text" as const, text: action === "edit" ? "Edit this image:" : "Animate this image. The movement should be:" },
        {
          type: "text" as const,
          text: `Source for this ${action} draft: Magic Hour image project ${projectId}, output ${outputNumber}, path ${new URL(downloadUrl).pathname}. Retrieve that project and use this exact output. This source item is not an edit or movement instruction; ask only for the missing instruction if needed. It does not authorize generation or saving. If several source items are ambiguous, ask which one to use.`,
          _meta: { "openai/title": `${name} · image ${outputNumber}` },
        },
      ],
      _meta: { "openai/message": { target: "active", send: false } },
    };
    setOpeningDraft(true);
    setDraftFeedback(null);
    try {
      const result = await app.sendMessage(draft);
      setDraftFeedback(result.isError ? "Could not open the draft. Try again." : `Draft for ${name}, image ${outputNumber}, is ready. Add your instructions in the chat, then send.`);
    } catch {
      setDraftFeedback("Could not open the draft. Try again.");
    } finally {
      setOpeningDraft(false);
    }
  };

  return (
    <main className="card" aria-live="polite">
      <section className="preview" aria-label="Generated media preview">
        <Preview key={`${projectId}:${downloadUrl}`} projectId={projectId} type={type} url={downloadUrl} name={name} status={status} message={message} />
        {canFullscreen && type !== "video" && (
          <button className="preview-fullscreen" type="button" onClick={requestFullscreen} aria-label="Fullscreen">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden="true"><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" /></svg>
          </button>
        )}
      </section>
      <section className="content">
        {status !== "complete" && <span className="badge" data-tone={tone}>{capitalize(status)}</span>}
        <h1>{name}</h1>
        {canCheckStatus && <button className="action action-secondary check-status" type="button" disabled={checkingProject === projectId} onClick={checkStatus}>
          {checkingProject === projectId ? "Checking…" : "Check status"}
        </button>}
        {canCheckStatus && statusFeedback?.id === projectId && <p className="draft-feedback" role="status">{statusFeedback.message}</p>}
        {urls.length > 1 && type === "image" && <div className="output-thumbnails" role="group" aria-label="Image outputs">
          {urls.map((url, index) => <button key={url} className="output-thumbnail" type="button" aria-label={`Select image ${index + 1} of ${urls.length}`} aria-pressed={index === selectedOutput} onClick={() => selectOutput(index)}>
            <img src={url} alt="" loading="lazy" />
          </button>)}
        </div>}
        {urls.length > 1 && type !== "image" && (
          <label className="output-picker">
            Output
            <select value={selectedOutput} onChange={(event) => selectOutput(Number(event.target.value))}>
              {urls.map((url, index) => <option key={url} value={index}>{index + 1} of {urls.length}</option>)}
            </select>
          </label>
        )}
        <div className="toolbar">
          <details className="details">
            <summary>Details</summary>
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
            {downloadUrl && <a className="action" href={downloadUrl} target="_blank" rel="noopener noreferrer" onClick={openDownload}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden="true"><path d="M12 3v12m-5-5 5 5 5-5M4 15v5h16v-5" /></svg>
              Download
            </a>}
          </div>
        </div>
        {canOpenImageDraft && <div className="image-actions">
          <button className="action action-secondary" type="button" disabled={openingDraft} onClick={() => openImageDraft("edit")}>Edit this</button>
          <button className="action action-secondary" type="button" disabled={openingDraft} onClick={() => openImageDraft("animate")}>Animate this</button>
        </div>}
        {draftFeedback && <p className="draft-feedback" role="status">{draftFeedback}</p>}
      </section>
    </main>
  );
}
