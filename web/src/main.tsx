import { inject as injectAnalytics } from "@vercel/analytics";
import { injectSpeedInsights } from "@vercel/speed-insights";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";
import { captureUiEvent } from "./analytics";

captureUiEvent("boot", "started");
window.addEventListener("error", () => captureUiEvent("runtime", "failed", { reason: "uncaught_error" }));
window.addEventListener("unhandledrejection", () => captureUiEvent("runtime", "failed", { reason: "unhandled_rejection" }));

const observabilityBasePath = `${new URL(import.meta.env.BASE_URL).origin}/app/observability`;
injectAnalytics({ basePath: observabilityBasePath });
injectSpeedInsights({ basePath: observabilityBasePath });

const root = document.getElementById("root");
if (!root) throw new Error("Missing root element");

createRoot(root, {
  onUncaughtError: (error) => {
    captureUiEvent("render", "failed", { reason: "uncaught_error" });
    console.error(error);
  },
  onRecoverableError: (error) => {
    captureUiEvent("render", "failed", { reason: "uncaught_error" });
    console.error(error);
  },
}).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
