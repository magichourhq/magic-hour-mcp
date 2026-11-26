import { useApp } from "@modelcontextprotocol/ext-apps/react";
import { useEffect, useEffectEvent, useRef, useState, type MouseEvent, type SyntheticEvent } from "react";
import { captureUiError, captureUiResult } from "./analytics";
import { draftReadyMessage, followUpsFor, sourceInstructionsFor, type FollowUp } from "./features/follow-ups/follow-ups";

type MediaType = "image" | "video" | "audio" | "media";
/** Media types whose finished results can open follow-up drafts. */
const DRAFT_MEDIA_TYPES: MediaType[] = ["image", "video", "audio"];
const UI_READ_ORIGIN = "magic-hour/ui-read";
type ReadAction = "status" | "preview" | "download";
type ResultState = {
  toolOutput: unknown;
  selectedOutput: number;
  contextRevision: number;
  previewRevision: number;
  pendingRead: { id: string; projectType: string; contextRevision: number; action: ReadAction } | null;
  feedback: { message: string; action: ReadAction; label?: string } | null;
};

const reloadStates: Record<string, { heading: string; body: string; buy: boolean; plan?: string }> = {
  auto_reload_pending: { heading: "Auto Reload is adding credits", body: "Your image, prompt and settings are kept here while the refill processes.", buy: false },
  payment_failed: { heading: "Auto Reload paused", body: "Your card was declined. Update your payment method to turn Auto Reload back on.", buy: false, plan: "Update payment method" },
  spending_limit_reached: { heading: "Auto Reload paused", body: "You've reached your monthly Auto Reload limit. Add credits to finish this video.", buy: true, plan: "Manage limit" },
};

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? value as Record<string, unknown>
    : {};
}

// Some failures (rate limits, outages, network errors) return only text. Without this the
// card would get an empty result and keep waiting for a project it will never see.
const TEXT_ONLY_ERROR_OUTPUT = {
  error: { message: "Magic Hour couldn't finish this step. The chat has the details." },
};
const FAILED_STATUSES = new Set(["error", "canceled", "cancelled"]);
const WAITING_STATUSES = new Set(["waiting", "queued", "rendering", "processing", "pending", "timeout"]);
const VIDEO_PENDING_HINT = "This card will show Play and Download when the video is ready.";
const SAVED_REQUEST_HINT = "Your request is saved. You can return here for the result.";

function isFailed(status: string): boolean {
  return FAILED_STATUSES.has(status);
}

function cardOutputFromCall(structuredContent: unknown, isError: unknown): unknown {
  if (Object.keys(record(structuredContent)).length) return structuredContent;
  return isError ? TEXT_ONLY_ERROR_OUTPUT : {};
}

function chatGptOutput(globals: unknown): unknown {
  const host = record(globals);
  const output = record(host.toolOutput);
  if (Object.keys(output).length) return output;
  const callResult = record(record(host.toolResponseMetadata).call_tool_result);
  return cardOutputFromCall(callResult.structuredContent, callResult.isError);
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
        return { ...current, pendingRead: null, feedback: { message: "Could not match this output. Your selection is unchanged.", action: "preview" } };
      }
      selectedOutput = nextPaths.indexOf(selectedPath);
    }
  }
  return { ...current, toolOutput, selectedOutput, contextRevision: current.contextRevision + 1, pendingRead: null, feedback: null };
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

// A wait call that times out still reports the render's last known status.
function liveStatusOf(status: string, project: Record<string, unknown>): string {
  if (status !== "timeout") return status;
  return optionalText(record(project.last_project).status)?.toLowerCase() ?? status;
}

function statusLabel(status: string): string {
  if (status === "timeout") return "Waiting";
  return capitalize(status);
}

type Tone = "success" | "danger" | "warning";

function toneOf(status: string): Tone {
  if (status === "complete") return "success";
  if (isFailed(status)) return "danger";
  return "warning";
}

function readBusyLabelFor(
  pendingRead: ResultState["pendingRead"], projectId: string | null, projectType: unknown, status: string,
): string {
  if (pendingRead?.action === "download") return "Preparing download…";
  if (pendingRead?.id !== projectId || pendingRead?.projectType !== projectType) return "Finishing check…";
  if (status === "complete") return "Loading your result…";
  return "Checking…";
}

type CardMessageInput = { project: Record<string, unknown>; status: string; tone: Tone; showVideoHint: boolean };

function cardMessage({ project, status, tone, showVideoHint }: CardMessageInput): string {
  if (showVideoHint) return VIDEO_PENDING_HINT;
  if (status === "timeout") return SAVED_REQUEST_HINT;
  return optionalText(project.message)
    ?? optionalText(project.error)
    ?? optionalText(record(project.error).message)
    ?? (tone === "danger" ? "Magic Hour could not complete this project." : "Magic Hour is preparing the result.");
}

function reportToolResult(result: unknown): void {
  const envelope = record(result);
  const project = record(envelope.structuredContent);
  // Tool and project failures are already captured by server instrumentation.
  const status = text(project.status).toLowerCase();
  if (envelope.isError || isFailed(status) || status === "timeout") return;
  const rawUrls = Array.isArray(project.exact_download_urls) ? project.exact_download_urls : [];
  if (!Object.keys(project).length) captureUiError("result_missing");
  else if (rawUrls.some((url) => !safeMediaUrl(url))) captureUiError("result_unsafe_download");
  else if (text(project.status).toLowerCase() === "complete" && !rawUrls.length) captureUiError("result_missing_download");
}

type PlaceholderProps = {
  icon: string;
  heading: string;
  message: string;
  onRetry?: () => void;
};

function Placeholder({ icon, heading, message, onRetry }: PlaceholderProps) {
  return (
    <div className="placeholder">
      <div className="placeholder-icon" aria-hidden="true">{icon}</div>
      <strong>{heading}</strong>
      <p>{message}</p>
      {onRetry && <button className="action action-secondary" type="button" onClick={onRetry}>Try again</button>}
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
  revision: number;
  canRecover: boolean;
  recovering: boolean;
  onRecover: () => Promise<boolean>;
  hasFeedback: boolean;
};

function Preview({ projectId, type, url, name, status, message, revision, canRecover, recovering, onRecover, hasFeedback }: PreviewProps) {
  const [failedRevision, setFailedRevision] = useState<string | null>(null);
  const automaticRecovery = useRef(false);
  const mediaRevision = `${url}:${revision}`;
  const loadFailed = failedRevision === mediaRevision;
  useEffect(() => {
    if (!loadFailed || !canRecover || recovering || automaticRecovery.current) return;
    automaticRecovery.current = true;
    void onRecover().then((started) => { if (!started) automaticRecovery.current = false; });
  }, [loadFailed, canRecover, recovering, onRecover]);
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
      setFailedRevision(mediaRevision);
    }
  };
  if (status !== "complete" || !url) {
    const failed = isFailed(status);
    const waitingHeading = type === "video" ? "Rendering your video" : "Waiting for result";
    const heading = failed ? "Project unavailable" : waitingHeading;
    return <Placeholder icon={failed ? "!" : "…"} heading={heading} message={message} />;
  }
  if (loadFailed) return <Placeholder icon={recovering ? "…" : "!"} heading={recovering ? "Loading your result" : "Preview unavailable"} message={recovering ? "Recovering your existing result." : canRecover ? "Try again, or use Download to get your result." : "Use Download to get your result."}
    onRetry={!recovering && canRecover && !hasFeedback && automaticRecovery.current ? () => { void onRecover(); } : undefined} />;
  if (type === "image") return <img key={mediaRevision} src={url} alt={name} onLoad={loaded} onError={failed} />;
  if (type === "video") return <video key={mediaRevision} src={url} controls playsInline preload="metadata" aria-label={name} onLoadedData={loaded} onPlaying={playing} onError={failed} />;
  if (type === "audio") {
    return (
      <div className="audio-preview">
        <div className="audio-mark" aria-hidden="true">♪</div>
        <audio key={mediaRevision} src={url} controls preload="metadata" aria-label={name} onLoadedData={loaded} onPlaying={playing} onError={failed} />
      </div>
    );
  }
  return <Placeholder icon="✓" heading="Project complete" message="Use Download to open the generated media." />;
}

export default function App() {
  const [readOrigin] = useState(() => crypto.randomUUID());
  const [result, setResult] = useState<ResultState>({ toolOutput: undefined, selectedOutput: 0, contextRevision: 0, previewRevision: 0, pendingRead: null, feedback: null });
  const { toolOutput, selectedOutput, previewRevision, pendingRead, feedback } = result;
  const activeRead = useRef<ResultState["pendingRead"]>(null);
  const [trackingRevision, setTrackingRevision] = useState(0);
  const [canFullscreen, setCanFullscreen] = useState(false);
  const [openingDraft, setOpeningDraft] = useState(false);
  const [draftFeedback, setDraftFeedback] = useState<string | null>(null);
  const { app, error: connectionError } = useApp({
    appInfo: { name: "Magic Hour project result", version: "1.0.0" },
    capabilities: {},
    onAppCreated: (createdApp) => {
      createdApp.ontoolresult = (result) => {
        if (result.isError || Object.keys(record(result.structuredContent)).length) {
          if (record(result.structuredContent)[UI_READ_ORIGIN] !== readOrigin) activeRead.current = null;
          setResult((current) => receiveToolOutput(current, cardOutputFromCall(result.structuredContent, result.isError), readOrigin));
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
      if (record(output)[UI_READ_ORIGIN] !== readOrigin) activeRead.current = null;
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
  const rawUrls = Array.isArray(project.exact_download_urls) ? project.exact_download_urls : [];
  const urls = projectUrls(project);
  const downloadUrl = urls[selectedOutput] ?? null;
  const projectId = optionalText(project.id);
  // A lookup the account cannot access has an error and no project id. Treat it as
  // unavailable so the card does not show a waiting state or start a status poll.
  const inaccessible = !projectId && project.error != null;
  const status = inaccessible ? "error" : text(project.status, "waiting").toLowerCase();
  const type = projectType(project, downloadUrl);
  const hostCapabilities = app?.getHostCapabilities();
  const chatGpt = (window as Window & { openai?: {
    openExternal?: (options: { href: string; redirectUrl: false }) => void | Promise<void>;
  } }).openai;
  /** The host can put an unsent draft in the chat (ChatGPT's `openai/message`). */
  const hostCanOpenDrafts = Boolean(hostCapabilities?.message?.text && hostCapabilities.experimental?.["openai/message"]);
  const canOpenDraft = Boolean(status === "complete" && DRAFT_MEDIA_TYPES.includes(type) && project.project_type === type
    && projectId && downloadUrl && hostCanOpenDrafts);
  /** Follow-up actions for this result: on the card and under More. */
  const followUps = followUpsFor(type, project.type);
  const showFollowUps = canOpenDraft && followUps.visible.length > 0;
  const canReadProject = Boolean(projectId && ["image", "video", "audio"].includes(String(project.project_type))
    && !isFailed(status) && hostCapabilities?.serverTools);
  const waitingForResult = WAITING_STATUSES.has(status);
  const liveStatus = liveStatusOf(status, project);
  const readBusyLabel = readBusyLabelFor(pendingRead, projectId, project.project_type, status);
  const name = text(project.name, inaccessible ? "Project unavailable" : `${capitalize(type)} project`);
  const prompt = optionalText(project.prompt) ?? optionalText(style.prompt);
  const model = optionalText(project.model) ?? optionalText(style.model);
  const tone = toneOf(status);
  const message = cardMessage({ project, status, tone, showVideoHint: type === "video" && waitingForResult && canReadProject });

  const openDownload = async (event: MouseEvent<HTMLAnchorElement>) => {
    if (projectId && downloadUrl && type !== "media") void captureUiResult("download_requested", projectId, type, downloadUrl);
    if (!app || !downloadUrl) return;
    const capabilities = app.getHostCapabilities();
    if (!chatGpt?.openExternal && !capabilities?.downloadFile && !capabilities?.openLinks) return;
    event.preventDefault();
    await readProject("download");
  };

  const readProject = async (action: ReadAction = "status"): Promise<boolean> => {
    if (!app || activeRead.current || (action !== "download" && !canReadProject)) return false;
    const id = projectId ?? "";
    const projectType = String(project.project_type);
    const request = { id, projectType, contextRevision: result.contextRevision, action };
    activeRead.current = request;
    const refreshing = status === "complete";
    const originalPaths = urls.map((url) => new URL(url).pathname);
    const selectedPath = originalPaths[selectedOutput];
    const failureMessage = action === "download" ? "Could not download your result. Try again."
      : refreshing ? "Could not load your result. Try again, or use Download."
      : "Could not check this project's status. Try again.";
    setResult((current) => ({ ...current, pendingRead: request, feedback: null }));
    const download = async (url: string): Promise<boolean> => {
      try {
        if (chatGpt?.openExternal) {
          // ChatGPT's standard open-link flow appends a query parameter that invalidates signed URLs.
          await chatGpt.openExternal({ href: url, redirectUrl: false });
          return true;
        }
        const response = hostCapabilities?.downloadFile
          ? await app.downloadFile({ contents: [{ type: "resource_link", uri: url, name }] })
          : await app.openLink({ url });
        if (!response.isError) return true;
        captureUiError("download_rejected");
      } catch {
        captureUiError("download_failed");
      }
      return false;
    };
    try {
      if (action === "download" && downloadUrl) {
        const index = rawUrls.findIndex((url) => safeMediaUrl(url) === downloadUrl);
        const expiration = (Array.isArray(project.download_expiration_metadata) ? project.download_expiration_metadata : [])
          .map(record).find((item) => String(item.download_index) === String(index))?.expires_at;
        const expired = typeof expiration === "string" && Date.parse(expiration) <= Date.now() + 30_000;
        if ((!expired || !canReadProject) && await download(downloadUrl)) return true;
      }
      if (activeRead.current !== request) return true;
      if (!canReadProject) throw new Error("Project recovery unavailable");
      const response = await app.callServerTool({
        name: `wait_for_${projectType}_project`,
        arguments: { id, timeout_seconds: 5, include_inline_downloads: false, max_inline_downloads: 0 },
        _meta: { [UI_READ_ORIGIN]: readOrigin },
      });
      if (activeRead.current !== request) return true;
      const candidate = record(response.structuredContent);
      const refreshedUrls = Array.isArray(candidate.exact_download_urls) ? candidate.exact_download_urls.map(safeMediaUrl) : [];
      const refreshedPaths = refreshedUrls.map((url) => url ? new URL(url).pathname : null);
      if (response.isError || candidate.id !== id || candidate.project_type !== projectType
        || (refreshing && !(candidate.status === "complete" && refreshedPaths.length > 0 && !refreshedPaths.includes(null)
          && new Set(originalPaths).size === originalPaths.length
          && originalPaths.every((path) => refreshedPaths.filter((candidate) => candidate === path).length === 1)))) throw new Error("Result identity mismatch");
      setResult((current) => {
        const latest = record(current.toolOutput);
        if (current.pendingRead !== request || current.contextRevision !== request.contextRevision || latest.id !== id || latest.project_type !== projectType) return current;
        const currentPath = originalPaths[current.selectedOutput];
        return {
          ...current,
          toolOutput: candidate,
          selectedOutput: refreshing && currentPath ? refreshedPaths.indexOf(currentPath) : 0,
          contextRevision: current.contextRevision + 1,
          previewRevision: current.previewRevision + (action === "preview" ? 1 : 0),
        };
      });
      if (action === "download") {
        const refreshedUrl = refreshedUrls[refreshedPaths.indexOf(selectedPath)];
        if (!refreshedUrl || !await download(refreshedUrl)) throw new Error("Download unavailable");
      }
    } catch {
      if (activeRead.current === request) setResult((current) => current.pendingRead === request
        ? { ...current, feedback: { message: failureMessage, action } } : current);
    } finally {
      if (activeRead.current === request) {
        activeRead.current = null;
        setResult((current) => current.pendingRead === request ? { ...current, pendingRead: null } : current);
      }
    }
    return true;
  };

  const trackProject = useEffectEvent(async (action: "resume" | "check" | "pause") => {
    if (!canReadProject || (action !== "resume" && (!waitingForResult || feedback))) return "stop";
    if (action === "pause") {
      setResult((current) => ({ ...current, feedback: { message: "This is taking longer than expected. You can keep checking this result.", action: "status", label: "Continue checking" } }));
      return "stop";
    }
    if (activeRead.current) return "busy";
    await readProject();
    return "checked";
  });

  useEffect(() => {
    if (!canReadProject) return;
    let disposed = false;
    let generation = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let startedAt = 0;
    const check = async (resume: boolean, currentGeneration: number) => {
      if (disposed || currentGeneration !== generation || document.hidden) return;
      if (Date.now() - startedAt >= 180_000) {
        await trackProject("pause");
        return;
      }
      const outcome = await trackProject(resume ? "resume" : "check");
      if (disposed || currentGeneration !== generation || document.hidden || outcome === "stop") return;
      timer = setTimeout(() => { void check(resume && outcome === "busy", currentGeneration); }, Date.now() - startedAt < 30_000 ? 2_000 : 10_000);
    };
    const visibilityChanged = () => {
      generation += 1;
      clearTimeout(timer);
      if (!document.hidden) {
        startedAt = Date.now();
        void check(true, generation);
      }
    };
    document.addEventListener("visibilitychange", visibilityChanged);
    visibilityChanged();
    return () => {
      disposed = true;
      generation += 1;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visibilityChanged);
    };
  }, [projectId, project.project_type, canReadProject, trackingRevision]);

  const selectOutput = (index: number) => {
    activeRead.current = null;
    setResult((current) => ({ ...current, selectedOutput: index, pendingRead: null, feedback: null }));
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

  /**
   * Opens an unsent draft in the same ChatGPT conversation for the selected output.
   * It never calls a tool, so nothing is generated or charged until the user sends it.
   * @param action The follow-up action the user clicked.
   */
  const openDraft = async (action: FollowUp) => {
    if (!app || !canOpenDraft || !projectId || !downloadUrl || openingDraft) return;
    const outputNumber = rawUrls.findIndex((value) => safeMediaUrl(value) === downloadUrl) + 1;
    const sourceInstructions = sourceInstructionsFor(type);
    const draft = {
      role: "user" as const,
      content: [
        { type: "text" as const, text: action.instruction },
        {
          type: "text" as const,
          text: `Source for this ${action.label} draft: Magic Hour ${type} project ${projectId}, output ${outputNumber}, path ${new URL(downloadUrl).pathname}. Retrieve that project and use this exact output. ${sourceInstructions} If several source items are ambiguous, ask which one to use.`,
          _meta: { "openai/title": `${name} · ${type} ${outputNumber}` },
        },
      ],
      _meta: { "openai/message": { target: "active", send: false } },
    };
    setOpeningDraft(true);
    setDraftFeedback(null);
    try {
      const result = await app.sendMessage(draft);
      setDraftFeedback(result.isError ? "Could not open the draft. Try again." : draftReadyMessage(name, type, outputNumber, action));
    } catch {
      setDraftFeedback("Could not open the draft. Try again.");
    } finally {
      setOpeningDraft(false);
    }
  };

  const apiError = record(project.error);
  if (apiError.code === "insufficient_credits") {
    const shortfall = typeof apiError.credit_shortfall === "number" ? apiError.credit_shortfall : null;
    const reason = String(record(apiError.auto_reload).reason);
    const openPage = async (href: string) => {
      if (chatGpt?.openExternal) await chatGpt.openExternal({ href, redirectUrl: false });
      else await app?.openLink({ url: href });
    };
    const addCredits = async () => {
      if (openingDraft) return;
      setOpeningDraft(true);
      // Stamped like the wait reads so a host echo of this result cannot replace the card.
      const checkout = await app?.callServerTool({ name: "create_credit_checkout", arguments: shortfall ? { credit_shortfall: shortfall } : {}, _meta: { [UI_READ_ORIGIN]: readOrigin } }).catch(() => null);
      const checkoutUrl = checkout?.isError ? null : optionalText(record(checkout?.structuredContent).checkout_url);
      const prefilled = Boolean(checkoutUrl?.startsWith("https://pay.magichour.ai/"));
      if (!prefilled) captureUiError("checkout_fallback");
      const href = prefilled && checkoutUrl ? checkoutUrl : "https://magichour.ai/pricing#credit-pack";
      await openPage(href).finally(() => setOpeningDraft(false));
    };
    const resume = async () => {
      const sent = await app?.sendMessage({ role: "user", content: [{ type: "text", text: `${reason in reloadStates ? "" : "I added credits. "}Continue with the same request and the same settings.` }] }).catch(() => null);
      setDraftFeedback(sent && !sent.isError ? null : "Could not continue. Ask ChatGPT to continue with the same request.");
    };
    const reload = reloadStates[reason] ?? { heading: "Add credits to continue", body: "Nothing was generated or charged.", buy: true };
    return (
      <main className="card" aria-live="polite">
        <section className="content">
          <h1>{reload.heading}</h1>
          <p className="draft-feedback">{shortfall ? `This request needs ${shortfall} more credits.` : "This request needs more credits."} {reload.body}</p>
          <div className="media-actions">
            {reload.buy && <button className="action" type="button" disabled={openingDraft} onClick={addCredits}>{openingDraft ? "Opening checkout…" : "Add credits"}</button>}
            {reload.plan && <button className="action action-secondary" type="button" onClick={() => void openPage("https://magichour.ai/my-plan")}>{reload.plan}</button>}
            <button className="action action-secondary" type="button" onClick={resume}>Continue</button>
          </div>
          {draftFeedback && <p className="draft-feedback" role="status">{draftFeedback}</p>}
        </section>
      </main>
    );
  }

  // A create result has an id but no project_type or status, so it cannot be polled.
  if (projectId && !project.project_type && !project.status) {
    return (
      <main className="card" aria-live="polite">
        <section className="content">
          <h1>Generation started</h1>
          <p className="draft-feedback">Your result will appear in this chat when it is ready.</p>
        </section>
      </main>
    );
  }

  return (
    <main className="card" aria-live="polite">
      <section className="preview" aria-label="Generated media preview">
        <Preview
          key={`${projectId}:${project.project_type}:${downloadUrl ? new URL(downloadUrl).pathname : ""}`}
          projectId={projectId} type={type} url={downloadUrl} name={name} status={status} message={message}
          revision={previewRevision} canRecover={canReadProject && status === "complete"} recovering={pendingRead !== null}
          onRecover={() => readProject("preview")} hasFeedback={feedback !== null}
        />
        {canFullscreen && type !== "video" && (
          <button className="preview-fullscreen" type="button" onClick={requestFullscreen} aria-label="Fullscreen">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden="true"><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" /></svg>
          </button>
        )}
      </section>
      <section className="content">
        {status !== "complete" && <span className="badge" data-tone={tone}>{statusLabel(liveStatus)}</span>}
        <h1>{name}</h1>
        {canReadProject && status !== "complete" && !waitingForResult && <button className="action action-secondary check-status" type="button" disabled={pendingRead !== null} onClick={() => setTrackingRevision((revision) => revision + 1)}>
          {pendingRead ? readBusyLabel : "Check status"}
        </button>}
        {status === "complete" && pendingRead && <p className="draft-feedback" role="status">{readBusyLabel}</p>}
        {feedback && <div className="draft-feedback" role="status">
          <p>{feedback.message}</p>
          {(canReadProject || feedback.action === "download") && <button className="action action-secondary" type="button" disabled={pendingRead !== null} onClick={() => feedback.action === "status" ? setTrackingRevision((revision) => revision + 1) : void readProject(feedback.action)}>{feedback.label ?? "Try again"}</button>}
        </div>}
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
        {showFollowUps && <div className="media-actions" role="group" aria-label="Follow-up actions">
          {followUps.visible.map((action) => (
            <button key={action.id} className="action action-secondary" type="button" disabled={openingDraft} onClick={() => openDraft(action)}>{action.label}</button>
          ))}
        </div>}
        {draftFeedback && <p className="draft-feedback" role="status">{draftFeedback}</p>}
      </section>
    </main>
  );
}
