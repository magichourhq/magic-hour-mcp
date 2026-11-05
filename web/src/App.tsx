import { useApp } from "@modelcontextprotocol/ext-apps/react";
import { useEffect, useState, type MouseEvent, type SyntheticEvent } from "react";
import { captureUiError, captureUiResult } from "./analytics";

type MediaType = "image" | "video" | "audio" | "media";
const UI_READ_ORIGIN = "magic-hour/ui-read";
type ResultState = {
  toolOutput: unknown;
  selectedOutput: number;
  contextRevision: number;
  previewRevision: number;
  pendingRead: { id: string; projectType: string; contextRevision: number } | null;
  feedback: string | null;
};

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

function projectUrls(project: Record<string, unknown>): string[] {
  const values = Array.isArray(project.exact_download_urls) ? project.exact_download_urls : [];
  return values.flatMap((value) => {
    const url = safeMediaUrl(value);
    return url ? [url] : [];
  });
}

function receiveToolOutput(current: ResultState, toolOutput: unknown, readOrigin: string): ResultState {
  const previous = record(current.toolOutput);
  const next = record(toolOutput);
  // Only the direct response may apply this widget's reads, even after a delayed host echo.
  if (next[UI_READ_ORIGIN] === readOrigin) return current;
  const sameProject = Boolean(optionalText(previous.id) && previous.id === next.id && previous.project_type === next.project_type);
  let selectedOutput = 0;
  if (sameProject && previous.status === "complete" && next.status === "complete") {
    const previousPaths = projectUrls(previous).map((url) => new URL(url).pathname);
    const selectedPath = previousPaths[current.selectedOutput];
    if (selectedPath) {
      const nextPaths = projectUrls(next).map((url) => new URL(url).pathname);
      if (previousPaths.filter((path) => path === selectedPath).length !== 1 || nextPaths.filter((path) => path === selectedPath).length !== 1) {
        return { ...current, feedback: "Could not match the refreshed output. The current selection is unchanged." };
      }
      selectedOutput = nextPaths.indexOf(selectedPath);
    }
  }
  return { ...current, toolOutput, selectedOutput, contextRevision: current.contextRevision + 1, feedback: null };
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
  canRefreshLink: boolean;
};

function Preview({ projectId, type, url, name, status, message, canRefreshLink }: PreviewProps) {
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
  if (loadFailed) return <Placeholder icon="!" heading="Preview unavailable" message={canRefreshLink ? "Use Refresh link to retrieve this result again, or try Download." : "Try Download, or ask to refresh this result's link."} />;
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
  const [readOrigin] = useState(() => crypto.randomUUID());
  const [result, setResult] = useState<ResultState>({ toolOutput: undefined, selectedOutput: 0, contextRevision: 0, previewRevision: 0, pendingRead: null, feedback: null });
  const { toolOutput, selectedOutput, previewRevision, pendingRead, feedback } = result;
  const [canFullscreen, setCanFullscreen] = useState(false);
  const [openingDraft, setOpeningDraft] = useState(false);
  const [draftFeedback, setDraftFeedback] = useState<string | null>(null);
  const { app, error: connectionError } = useApp({
    appInfo: { name: "Magic Hour project result", version: "1.0.0" },
    capabilities: {},
    onAppCreated: (createdApp) => {
      createdApp.ontoolresult = (result) => {
        if (result.isError || Object.keys(record(result.structuredContent)).length) {
          setResult((current) => receiveToolOutput(current, result.structuredContent ?? {}, readOrigin));
          setDraftFeedback(null);
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
      setResult((current) => receiveToolOutput(current, output, readOrigin));
      setDraftFeedback(null);
    };
    window.addEventListener("openai:set_globals", readHostOutput);
    readHostOutput();
    return () => window.removeEventListener("openai:set_globals", readHostOutput);
  }, [readOrigin]);

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
  const urls = projectUrls(project);
  const downloadUrl = urls[selectedOutput] ?? null;
  const projectId = optionalText(project.id);
  const type = projectType(project, downloadUrl);
  const hostCapabilities = app?.getHostCapabilities();
  const canOpenDraft = Boolean(status === "complete" && ["image", "video"].includes(type) && project.project_type === type && projectId && downloadUrl
    && hostCapabilities?.message?.text && hostCapabilities.experimental?.["openai/message"]);
  const canReadProject = Boolean(projectId && ["image", "video", "audio"].includes(String(project.project_type))
    && !["error", "canceled", "cancelled"].includes(status) && hostCapabilities?.serverTools);
  const readBusyLabel = pendingRead?.id !== projectId || pendingRead?.projectType !== project.project_type
    ? "Finishing check…" : status === "complete" ? "Refreshing…" : "Checking…";
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

  const readProject = async () => {
    if (!app || !canReadProject || !projectId || pendingRead) return;
    const id = projectId;
    const projectType = String(project.project_type);
    const request = { id, projectType, contextRevision: result.contextRevision };
    const refreshing = status === "complete";
    const originalPaths = urls.map((url) => new URL(url).pathname);
    const failureMessage = refreshing
      ? "Could not refresh this result's links. The current selection is unchanged."
      : "Could not check this project's status. Try again.";
    setResult((current) => ({ ...current, pendingRead: request, feedback: null }));
    let updated: Record<string, unknown> | undefined;
    let refreshedPaths: (string | null)[] = [];
    try {
      const response = await app.callServerTool({
        name: `wait_for_${projectType}_project`,
        arguments: { id, timeout_seconds: 5, include_inline_downloads: false, max_inline_downloads: 0 },
        _meta: { [UI_READ_ORIGIN]: readOrigin },
      });
      const candidate = record(response.structuredContent);
      const refreshedUrls = Array.isArray(candidate.exact_download_urls) ? candidate.exact_download_urls.map(safeMediaUrl) : [];
      refreshedPaths = refreshedUrls.map((url) => url ? new URL(url).pathname : null);
      if (!response.isError && candidate.id === id && candidate.project_type === projectType
        && (!refreshing || (candidate.status === "complete" && refreshedPaths.length > 0 && !refreshedPaths.includes(null)
          && new Set(originalPaths).size === originalPaths.length
          && originalPaths.every((path) => refreshedPaths.filter((candidate) => candidate === path).length === 1)))) {
        updated = candidate;
      }
    } catch {
      // Preserve the existing result and show the same bounded recovery message.
    }
    setResult((current) => {
      const latest = record(current.toolOutput);
      if (current.pendingRead !== request) return current;
      if (current.contextRevision !== request.contextRevision || latest.id !== id || latest.project_type !== projectType) return { ...current, pendingRead: null };
      if (!updated) return { ...current, pendingRead: null, feedback: failureMessage };
      const selectedPath = originalPaths[current.selectedOutput];
      return {
        toolOutput: updated,
        selectedOutput: refreshing && selectedPath ? refreshedPaths.indexOf(selectedPath) : 0,
        contextRevision: current.contextRevision + 1,
        previewRevision: current.previewRevision + (refreshing ? 1 : 0),
        pendingRead: null,
        feedback: null,
      };
    });
  };

  const selectOutput = (index: number) => {
    setResult((current) => ({ ...current, selectedOutput: index, feedback: null }));
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

  const openDraft = async (action: "edit" | "animate" | "captions" | "edit-video" | "translate") => {
    if (!app || !canOpenDraft || !projectId || !downloadUrl || openingDraft) return;
    const outputNumber = rawUrls.findIndex((value) => safeMediaUrl(value) === downloadUrl) + 1;
    const instruction = {
      edit: "Edit this image:",
      animate: "Animate this image. The movement should be:",
      captions: "Add captions to this video, preserving the full clip.",
      "edit-video": "Edit this video:",
      translate: "Translate this video's speech into:",
    }[action];
    const sourceInstructions = type === "video"
      ? "Read the actual clip duration before setting segment times. Preserve the full clip unless the user requests a segment; explain any unsupported requirements instead of silently trimming. This source item is not a target language or edit instruction; ask only for missing instructions if needed. This source binding alone does not authorize generation or saving."
      : "This source item is not an edit or movement instruction; ask only for the missing instruction if needed. It does not authorize generation or saving.";
    const draft = {
      role: "user" as const,
      content: [
        { type: "text" as const, text: instruction },
        {
          type: "text" as const,
          text: `Source for this ${action} draft: Magic Hour ${type} project ${projectId}, output ${outputNumber}, path ${new URL(downloadUrl).pathname}. Retrieve that project and use this exact output. ${sourceInstructions} If several source items are ambiguous, ask which one to use.`,
          _meta: { "openai/title": `${name} · ${type} ${outputNumber}` },
        },
      ],
      _meta: { "openai/message": { target: "active", send: false } },
    };
    setOpeningDraft(true);
    setDraftFeedback(null);
    try {
      const result = await app.sendMessage(draft);
      setDraftFeedback(result.isError ? "Could not open the draft. Try again." : `Draft for ${name}, ${type} ${outputNumber}, is ready. ${type === "image" ? "Add your instructions" : "Review it"} in the chat, then send.`);
    } catch {
      setDraftFeedback("Could not open the draft. Try again.");
    } finally {
      setOpeningDraft(false);
    }
  };

  return (
    <main className="card" aria-live="polite">
      <section className="preview" aria-label="Generated media preview">
        <Preview key={`${projectId}:${downloadUrl}:${previewRevision}`} projectId={projectId} type={type} url={downloadUrl} name={name} status={status} message={message} canRefreshLink={canReadProject && status === "complete"} />
        {canFullscreen && type !== "video" && (
          <button className="preview-fullscreen" type="button" onClick={requestFullscreen} aria-label="Fullscreen">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden="true"><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" /></svg>
          </button>
        )}
      </section>
      <section className="content">
        {status !== "complete" && <span className="badge" data-tone={tone}>{capitalize(status)}</span>}
        <h1>{name}</h1>
        {canReadProject && <button className="action action-secondary check-status" type="button" disabled={pendingRead !== null} onClick={readProject}>
          {pendingRead ? readBusyLabel : status === "complete" ? "Refresh link" : "Check status"}
        </button>}
        {canReadProject && feedback && <p className="draft-feedback" role="status">{feedback}</p>}
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
        {canOpenDraft && <div className="media-actions">
          {type === "image" ? <>
            <button className="action action-secondary" type="button" disabled={openingDraft} onClick={() => openDraft("edit")}>Edit this</button>
            <button className="action action-secondary" type="button" disabled={openingDraft} onClick={() => openDraft("animate")}>Animate this</button>
          </> : <>
            <button className="action action-secondary" type="button" disabled={openingDraft} onClick={() => openDraft("captions")}>Add captions</button>
            <button className="action action-secondary" type="button" disabled={openingDraft} onClick={() => openDraft("edit-video")}>Edit this video</button>
            <button className="action action-secondary" type="button" disabled={openingDraft} onClick={() => openDraft("translate")}>Translate</button>
          </>}
        </div>}
        {draftFeedback && <p className="draft-feedback" role="status">{draftFeedback}</p>}
      </section>
    </main>
  );
}
