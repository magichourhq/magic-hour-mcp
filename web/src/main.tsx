import { inject as injectAnalytics } from "@vercel/analytics";
import { injectSpeedInsights } from "@vercel/speed-insights";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

const observabilityBasePath = `${new URL(import.meta.env.BASE_URL).origin}/app/observability`;
injectAnalytics({ basePath: observabilityBasePath });
injectSpeedInsights({ basePath: observabilityBasePath });

const root = document.getElementById("root");
if (!root) throw new Error("Missing root element");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
