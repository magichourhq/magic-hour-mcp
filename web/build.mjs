import { build } from "esbuild";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.dirname(fileURLToPath(import.meta.url));
const outputDirectory = path.join(root, "..", "mcp_magichour", "static");

const [script, style, template] = await Promise.all([
  build({
    bundle: true,
    entryPoints: [path.join(root, "src", "project-result.js")],
    format: "iife",
    minify: true,
    platform: "browser",
    target: "es2022",
    write: false,
  }),
  build({
    bundle: true,
    entryPoints: [path.join(root, "src", "project-result.css")],
    minify: true,
    write: false,
  }),
  readFile(path.join(root, "src", "project-result.html"), "utf8"),
]);

const html = template
  .replace("/* APP_STYLE */", style.outputFiles[0].text.trim())
  .replace("/* APP_SCRIPT */", script.outputFiles[0].text.trim());

await mkdir(outputDirectory, { recursive: true });
await writeFile(path.join(outputDirectory, "project-result.html"), html);
