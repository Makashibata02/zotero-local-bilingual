import { build } from "esbuild";
import { cp, mkdir, rm, readFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL(".", import.meta.url));
const manifest = JSON.parse(await readFile(join(root, "addon", "manifest.json"), "utf8"));
const settings = manifest.applications?.zotero;
if (!settings?.id || !settings?.strict_max_version ||
    !settings?.update_url?.startsWith("https://")) {
  throw new Error("Zotero requires an id, strict_max_version and HTTPS update_url");
}
const output = join(root, "build");
await rm(output, { recursive: true, force: true });
await mkdir(join(output, "content", "scripts"), { recursive: true });
await cp(join(root, "addon"), output, { recursive: true });
await build({
  entryPoints: [join(root, "src", "main.ts")],
  outfile: join(output, "content", "scripts", "local-bilingual.js"),
  bundle: true,
  define: {__PLUGIN_VERSION__: JSON.stringify(manifest.version)},
  format: "iife",
  globalName: "LocalBilingualBundle",
  target: "firefox128",
  sourcemap: false,
  legalComments: "eof"
});
