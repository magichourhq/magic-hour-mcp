from __future__ import annotations

import base64
import hashlib

MCP_APP_VIEW_URI = "ui://magic-hour/project-result-v1.html"
MCP_APP_VIEW_PATH = "/app/project-result"
MCP_APP_VIEW_URL = f"https://mcp.magichour.ai{MCP_APP_VIEW_PATH}"
MCP_APP_MEDIA_ORIGIN = "https://videos.magichour.ai"

MCP_APP_VIEW_STYLE = """
:root {
  color-scheme: light dark;
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --panel: #ffffff;
  --panel-muted: #f2f2ef;
  --text: #20201e;
  --muted: #6f6f68;
  --line: #deded8;
  --accent: #6d43e5;
  --accent-text: #ffffff;
  --success: #18794e;
  --warning: #9a6700;
  --danger: #c9372c;
}

@media (prefers-color-scheme: dark) {
  :root {
    --panel: #222220;
    --panel-muted: #2c2c29;
    --text: #f4f4ef;
    --muted: #abab9f;
    --line: #3e3e38;
    --accent: #9b7cf3;
    --accent-text: #171716;
    --success: #68d5a0;
    --warning: #e5b95c;
    --danger: #ff8f86;
  }
}

* { box-sizing: border-box; }
html, body { margin: 0; background: transparent; color: var(--text); }
body { padding: 1px; }
button, a { font: inherit; }
.card {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(245px, .85fr);
  width: 100%;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: var(--panel);
}
.preview {
  position: relative;
  display: grid;
  min-height: 330px;
  overflow: hidden;
  place-items: center;
  background: var(--panel-muted);
}
.preview img, .preview video {
  display: block;
  width: 100%;
  height: 100%;
  min-height: 330px;
  object-fit: contain;
  background: #111110;
}
.audio-preview {
  display: grid;
  width: min(82%, 380px);
  gap: 24px;
  place-items: center;
}
.audio-mark {
  display: grid;
  width: 104px;
  height: 104px;
  place-items: center;
  border-radius: 50%;
  background: var(--accent);
  color: var(--accent-text);
  font-size: 48px;
}
.audio-preview audio { width: 100%; }
.placeholder { width: min(82%, 360px); text-align: center; }
.placeholder-icon { margin: 0 0 10px; font-size: 34px; }
.placeholder strong { display: block; margin-bottom: 5px; }
.placeholder p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.45; }
.content { display: flex; min-width: 0; flex-direction: column; gap: 16px; padding: 20px; }
.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  width: fit-content;
  padding: 5px 9px;
  border-radius: 999px;
  background: var(--panel-muted);
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
}
.badge::before { width: 7px; height: 7px; border-radius: 50%; background: currentColor; content: ""; }
.badge[data-tone="success"] { color: var(--success); }
.badge[data-tone="warning"] { color: var(--warning); }
.badge[data-tone="danger"] { color: var(--danger); }
h1 { margin: 0; font-size: 18px; letter-spacing: -.02em; }
.summary { margin: 7px 0 0; color: var(--muted); font-size: 13px; line-height: 1.45; overflow-wrap: anywhere; }
.details { display: grid; gap: 10px; padding: 14px; border: 1px solid var(--line); border-radius: 12px; background: var(--panel-muted); }
.detail { display: flex; justify-content: space-between; gap: 12px; font-size: 12px; }
.detail dt { color: var(--muted); }
.detail dd { min-width: 0; margin: 0; overflow: hidden; font-weight: 650; text-align: right; text-overflow: ellipsis; white-space: nowrap; }
.actions { display: flex; margin-top: auto; }
.action {
  display: inline-flex;
  width: 100%;
  min-height: 40px;
  align-items: center;
  justify-content: center;
  padding: 0 15px;
  border: 1px solid var(--accent);
  border-radius: 10px;
  background: var(--accent);
  color: var(--accent-text);
  font-weight: 700;
  text-decoration: none;
}
.action[hidden] { display: none; }
.action:focus-visible { outline: 3px solid color-mix(in srgb, var(--accent) 45%, transparent); outline-offset: 2px; }

@media (max-width: 560px) {
  .card { grid-template-columns: 1fr; }
  .preview, .preview img, .preview video { min-height: 240px; }
  .preview { max-height: 420px; }
}
""".strip()

MCP_APP_VIEW_SCRIPT = r"""
(() => {
  const preview = document.getElementById("preview");
  const badge = document.getElementById("status");
  const title = document.getElementById("title");
  const summary = document.getElementById("summary");
  const projectId = document.getElementById("project-id");
  const mediaType = document.getElementById("media-type");
  const credits = document.getElementById("credits");
  const outputs = document.getElementById("outputs");
  const download = document.getElementById("download");
  let downloadUrl = null;

  function text(value, fallback = "—") {
    return typeof value === "string" && value.trim() ? value.trim() : fallback;
  }

  function capitalize(value) {
    const normalized = text(value, "media");
    return normalized.charAt(0).toUpperCase() + normalized.slice(1);
  }

  function safeMediaUrl(value) {
    if (typeof value !== "string") return null;
    try {
      const url = new URL(value);
      return url.protocol === "https:" && url.hostname === "videos.magichour.ai" ? url.href : null;
    } catch {
      return null;
    }
  }

  function projectType(result, url) {
    if (["image", "video", "audio"].includes(result?.project_type)) return result.project_type;
    const path = url ? new URL(url).pathname.toLowerCase() : "";
    if (/\.(png|jpe?g|webp|gif|avif|heic|tiff?)$/.test(path)) return "image";
    if (/\.(mp3|wav|aac|flac|m4a|ogg|opus|weba?)$/.test(path)) return "audio";
    if (/\.(mp4|m4v|mov|webm)$/.test(path)) return "video";
    return "media";
  }

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

  function render(result) {
    const project = result && typeof result === "object" ? result : {};
    const status = text(project.status, "waiting").toLowerCase();
    const urls = Array.isArray(project.exact_download_urls) ? project.exact_download_urls.map(safeMediaUrl).filter(Boolean) : [];
    downloadUrl = urls[0] || null;
    const type = projectType(project, downloadUrl);
    const name = text(project.name, `${capitalize(type)} project`);
    const message = text(project.message || project.error, status === "complete" ? "Generated media is ready." : "Magic Hour is preparing the result.");
    const prompt = text(project.prompt || project.style?.prompt, message);
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
    const message = event.data;
    if (!message || message.jsonrpc !== "2.0" || message.method !== "ui/notifications/tool-result") return;
    render(message.params?.structuredContent);
  }, { passive: true });

  window.addEventListener("openai:set_globals", (event) => {
    const next = event.detail?.globals?.toolOutput;
    if (next) render(next);
  }, { passive: true });

  download.addEventListener("click", (event) => {
    if (!downloadUrl || !window.openai?.openExternal) return;
    event.preventDefault();
    window.openai.openExternal({ href: downloadUrl, redirectUrl: false });
  });

  render(window.openai?.toolOutput);
})();
""".strip()


def _csp_hash(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


MCP_APP_VIEW_CSP = (
    "default-src 'none'; "
    "connect-src https://mcp.magichour.ai; "
    "frame-ancestors https://chatgpt.com https://claude.ai; "
    "form-action 'none'; "
    f"img-src {MCP_APP_MEDIA_ORIGIN}; "
    f"media-src {MCP_APP_MEDIA_ORIGIN}; "
    f"script-src 'sha256-{_csp_hash(MCP_APP_VIEW_SCRIPT)}'; "
    f"style-src 'sha256-{_csp_hash(MCP_APP_VIEW_STYLE)}'; "
    "base-uri https://mcp.magichour.ai"
)

MCP_APP_VIEW_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <base href="{MCP_APP_VIEW_URL}">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>Magic Hour project result</title>
  <style>{MCP_APP_VIEW_STYLE}</style>
</head>
<body>
  <main class="card" aria-live="polite">
    <section id="preview" class="preview" aria-label="Generated media preview"></section>
    <section class="content">
      <span id="status" class="badge" data-tone="warning">Waiting</span>
      <div>
        <h1 id="title">Magic Hour project</h1>
        <p id="summary" class="summary">Waiting for project result.</p>
      </div>
      <dl class="details">
        <div class="detail"><dt>Project</dt><dd id="project-id">—</dd></div>
        <div class="detail"><dt>Media</dt><dd id="media-type">—</dd></div>
        <div class="detail"><dt>Credits</dt><dd id="credits">—</dd></div>
        <div class="detail"><dt>Outputs</dt><dd id="outputs">0</dd></div>
      </dl>
      <div class="actions">
        <a id="download" class="action" href="#" target="_blank" rel="noopener noreferrer" hidden>Download</a>
      </div>
    </section>
  </main>
  <script>{MCP_APP_VIEW_SCRIPT}</script>
</body>
</html>"""
