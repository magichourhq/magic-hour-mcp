import { defineConfig } from "vite";

export default defineConfig({
  base: "https://mcp.magichour.ai/app/project-result-assets/",
  build: {
    emptyOutDir: true,
    outDir: "../mcp_magichour/static/project-result",
  },
});
