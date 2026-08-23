import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const deploymentHost = process.env.VERCEL_URL || process.env.VITE_VERCEL_URL || "mcp.magichour.ai";

export default defineConfig({
  base: `https://${deploymentHost}/app/project-result-assets/`,
  plugins: [react()],
  build: {
    emptyOutDir: true,
    outDir: "../mcp_magichour/static/project-result",
  },
});
