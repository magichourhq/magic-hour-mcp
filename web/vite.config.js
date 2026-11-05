import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Pin to the prod origin: VERCEL_URL is the per-deploy host, which the widget CSP blocks.
const appOrigin = (process.env.MCP_APP_ORIGIN || "https://mcp.magichour.ai").replace(/\/$/, "");

export default defineConfig({
  base: `${appOrigin}/app/project-result-assets/`,
  plugins: [react()],
  build: {
    emptyOutDir: true,
    outDir: "../mcp_magichour/static/project-result",
  },
});
