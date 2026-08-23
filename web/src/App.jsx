// @ts-check

import { useEffect, useState } from "react";

/** @typedef {{ toolOutput?: unknown, openExternal?: (options: { href: string, redirectUrl?: boolean }) => void }} OpenAIHost */

const hostWindow = /** @type {Window & { openai?: OpenAIHost }} */ (window);

/** @param {unknown} value */
function record(value) {
  return typeof value === "object" && value !== null
    ? /** @type {Record<string, unknown>} */ (value)
    : {};
}

/** @param {unknown} value @param {string} [fallback] */
function text(value, fallback = "—") {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

/** @param {unknown} value */
function capitalize(value) {
  const normalized = text(value, "media");
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

/** @param {unknown} value @returns {string | null} */
function safeMediaUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "videos.magichour.ai" ? url.href : null;
  } catch {
    return null;
  }
}

/** @param {Record<string, unknown>} result @param {string | null} url */
function projectType(result, url) {
  if (["image", "video", "audio"].includes(String(result.project_type))) return String(result.project_type);
  const path = url ? new URL(url).pathname.toLowerCase() : "";
  if (/\.(png|jpe?g|webp|gif|avif|heic|tiff?)$/.test(path)) return "image";
  if (/\.(mp3|wav|aac|flac|m4a|ogg|opus|weba?)$/.test(path)) return "audio";
  if (/\.(mp4|m4v|mov|webm)$/.test(path)) return "video";
  return "media";
}

/** @param {{ icon: string, heading: string, message: string }} props */
function Placeholder({ icon, heading, message }) {
  return (
    <div className="placeholder">
      <div className="placeholder-icon" aria-hidden="true">{icon}</div>
      <strong>{heading}</strong>
      <p>{message}</p>
    </div>
  );
}

/** @param {{ type: string, url: string | null, name: string, status: string, message: string }} props */
function Preview({ type, url, name, status, message }) {
  if (status !== "complete" || !url) {
    const failed = ["error", "canceled", "cancelled", "timeout"].includes(status);
    return <Placeholder icon={failed ? "!" : "…"} heading={failed ? "Project unavailable" : "Waiting for result"} message={message} />;
  }
  if (type === "image") return <img src={url} alt={name} />;
  if (type === "video") return <video src={url} controls playsInline preload="metadata" aria-label={name} />;
  if (type === "audio") {
    return (
      <div className="audio-preview">
        <div className="audio-mark" aria-hidden="true">♪</div>
        <audio src={url} controls preload="metadata" aria-label={name} />
      </div>
    );
  }
  return <Placeholder icon="✓" heading="Project complete" message="Use Download to open the generated media." />;
}

export default function App() {
  const [toolOutput, setToolOutput] = useState(hostWindow.openai?.toolOutput);

  useEffect(() => {
    /** @param {MessageEvent} event */
    const onMessage = (event) => {
      if (event.source !== window.parent) return;
      const message = record(event.data);
      if (message.jsonrpc !== "2.0" || message.method !== "ui/notifications/tool-result") return;
      setToolOutput(record(message.params).structuredContent);
    };
    /** @param {Event} event */
    const onGlobals = (event) => {
      const detail = record(/** @type {CustomEvent} */ (event).detail);
      const next = record(detail.globals).toolOutput;
      if (next) setToolOutput(next);
    };
    window.addEventListener("message", onMessage);
    window.addEventListener("openai:set_globals", onGlobals);
    return () => {
      window.removeEventListener("message", onMessage);
      window.removeEventListener("openai:set_globals", onGlobals);
    };
  }, []);

  const project = record(toolOutput);
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
  const message = text(project.message || project.error, status === "complete" ? "Generated media is ready." : "Magic Hour is preparing the result.");
  const prompt = text(project.prompt || style.prompt, message);
  const tone = status === "complete" ? "success" : ["error", "canceled", "cancelled", "timeout"].includes(status) ? "danger" : "warning";

  /** @param {import("react").MouseEvent<HTMLAnchorElement>} event */
  const openDownload = (event) => {
    if (!downloadUrl || !hostWindow.openai?.openExternal) return;
    event.preventDefault();
    hostWindow.openai.openExternal({ href: downloadUrl, redirectUrl: false });
  };

  return (
    <main className="card" aria-live="polite">
      <section className="preview" aria-label="Generated media preview">
        <Preview type={type} url={downloadUrl} name={name} status={status} message={message} />
      </section>
      <section className="content">
        <span className="badge" data-tone={tone}>{capitalize(status)}</span>
        <div>
          <h1>{name}</h1>
          <p className="summary">{prompt}</p>
        </div>
        <dl className="details">
          <div className="detail"><dt>Project</dt><dd>{text(project.id)}</dd></div>
          <div className="detail"><dt>Media</dt><dd>{capitalize(type)}</dd></div>
          <div className="detail"><dt>Credits</dt><dd>{project.credits_charged == null ? "—" : String(project.credits_charged)}</dd></div>
          <div className="detail"><dt>Outputs</dt><dd>{urls.length}</dd></div>
        </dl>
        <div className="actions">
          {downloadUrl && <a className="action" href={downloadUrl} target="_blank" rel="noopener noreferrer" onClick={openDownload}>Download</a>}
        </div>
      </section>
    </main>
  );
}
