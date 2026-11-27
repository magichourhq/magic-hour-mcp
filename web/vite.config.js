import { defineConfig, lazyPlugins } from "vite-plus";
import react from "@vitejs/plugin-react";

// Production pins to the prod origin; preview deploys use their own host so the bundle exists there.
// Must match MCP_APP_ORIGIN in mcp_magichour/project_result_app.py.
const appOrigin =
  process.env.VERCEL_ENV === "preview" && process.env.VERCEL_URL
    ? `https://${process.env.VERCEL_URL}`
    : "https://mcp.magichour.ai";

export default defineConfig({
  fmt: {},
  lint: {
    jsPlugins: [{ name: "vite-plus", specifier: "vite-plus/oxlint-plugin" }],
    rules: { "vite-plus/prefer-vite-plus-imports": "error" },
    options: { typeAware: true, typeCheck: true },
  },
  test: {
    // TODO: Remove once the first web test lands.
    passWithNoTests: true,
  },
  base: `${appOrigin}/app/project-result-assets/`,
  plugins: lazyPlugins(() => [react()]),
  build: {
    emptyOutDir: true,
    outDir: "../mcp_magichour/static/project-result",
  },
});
