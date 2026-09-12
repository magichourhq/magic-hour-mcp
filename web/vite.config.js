import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const deploymentHost = process.env.VERCEL_URL || process.env.VITE_VERCEL_URL || "mcp.magichour.ai";
const appOrigin = (process.env.MCP_APP_ORIGIN || `https://${deploymentHost}`).replace(/\/+$/, "");

export default defineConfig({
  base: `${appOrigin}/app/project-result-assets/`,
  plugins: [react()],
  build: {
    emptyOutDir: true,
    outDir: "../mcp_magichour/static/project-result",
  },
});
