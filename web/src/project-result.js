// @ts-check

/** @typedef {{ toolOutput?: unknown, openExternal?: (options: { href: string, redirectUrl?: boolean }) => void }} OpenAIHost */

const hostWindow = /** @type {Window & { openai?: OpenAIHost }} */ (window);
const preview = requiredElement("preview");
const badge = requiredElement("status");
const title = requiredElement("title");
const summary = requiredElement("summary");
const projectId = requiredElement("project-id");
const mediaType = requiredElement("media-type");
const credits = requiredElement("credits");
const outputs = requiredElement("outputs");
const download = /** @type {HTMLAnchorElement} */ (requiredElement("download"));
/** @type {string | null} */
let downloadUrl = null;

/** @param {string} id */
function requiredElement(id) {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing required element: ${id}`);
  return element;
}

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

/** @param {string} icon @param {string} heading @param {string} message */
function placeholder(icon, heading, message) {
  const wrapper = document.createElement("div");
  wrapper.className = "placeholder";
  const mark = document.createElement("div");
  mark.className = "placeholder-icon";
  mark.setAttribute("aria-hidden", "true");
  mark.textContent = icon;
  const strong = document.createElement("strong");
  strong.textContent = heading;
  const body = document.createElement("p");
  body.textContent = message;
  wrapper.append(mark, strong, body);
  return wrapper;
}

/** @param {string} type @param {string | null} url @param {string} name @param {string} status @param {string} message */
function renderPreview(type, url, name, status, message) {
  preview.replaceChildren();
  if (status !== "complete" || !url) {
    const failed = ["error", "canceled", "cancelled", "timeout"].includes(status);
    preview.append(placeholder(failed ? "!" : "…", failed ? "Project unavailable" : "Waiting for result", message));
    return;
  }

  if (type === "image") {
    const image = document.createElement("img");
    image.src = url;
    image.alt = name;
    preview.append(image);
    return;
  }

  if (type === "video") {
    const video = document.createElement("video");
    video.src = url;
    video.controls = true;
    video.playsInline = true;
    video.preload = "metadata";
    video.setAttribute("aria-label", name);
    preview.append(video);
    return;
  }

  if (type === "audio") {
    const wrapper = document.createElement("div");
    wrapper.className = "audio-preview";
    const mark = document.createElement("div");
    mark.className = "audio-mark";
    mark.setAttribute("aria-hidden", "true");
    mark.textContent = "♪";
    const audio = document.createElement("audio");
    audio.src = url;
    audio.controls = true;
    audio.preload = "metadata";
    audio.setAttribute("aria-label", name);
    wrapper.append(mark, audio);
    preview.append(wrapper);
    return;
  }

  preview.append(placeholder("✓", "Project complete", "Use Download to open the generated media."));
}

/** @param {unknown} result */
function render(result) {
  const project = record(result);
  const style = record(project.style);
  const status = text(project.status, "waiting").toLowerCase();
  const rawUrls = Array.isArray(project.exact_download_urls) ? project.exact_download_urls : [];
  const urls = rawUrls.map(safeMediaUrl).filter((url) => url !== null);
  downloadUrl = urls[0] ?? null;
  const type = projectType(project, downloadUrl);
  const name = text(project.name, `${capitalize(type)} project`);
  const message = text(project.message || project.error, status === "complete" ? "Generated media is ready." : "Magic Hour is preparing the result.");
  const prompt = text(project.prompt || style.prompt, message);
  const tone = status === "complete" ? "success" : ["error", "canceled", "cancelled", "timeout"].includes(status) ? "danger" : "warning";

  badge.textContent = capitalize(status);
  badge.dataset.tone = tone;
  title.textContent = name;
  summary.textContent = prompt;
  projectId.textContent = text(project.id);
  mediaType.textContent = capitalize(type);
  credits.textContent = project.credits_charged == null ? "—" : String(project.credits_charged);
  outputs.textContent = String(urls.length);
  download.hidden = !downloadUrl;
  if (downloadUrl) download.href = downloadUrl;
  else download.removeAttribute("href");
  renderPreview(type, downloadUrl, name, status, message);
}

window.addEventListener("message", (event) => {
  if (event.source !== window.parent) return;
  const message = record(event.data);
  if (message.jsonrpc !== "2.0" || message.method !== "ui/notifications/tool-result") return;
  render(record(message.params).structuredContent);
}, { passive: true });

window.addEventListener("openai:set_globals", (event) => {
  const detail = record(/** @type {CustomEvent} */ (event).detail);
  const next = record(detail.globals).toolOutput;
  if (next) render(next);
}, { passive: true });

download.addEventListener("click", (event) => {
  if (!downloadUrl || !hostWindow.openai?.openExternal) return;
  event.preventDefault();
  hostWindow.openai.openExternal({ href: downloadUrl, redirectUrl: false });
});

render(hostWindow.openai?.toolOutput);
