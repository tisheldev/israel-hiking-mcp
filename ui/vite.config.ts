/// <reference types="vitest/config" />
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig, type Plugin } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// This package is `"type": "module"`, so there is no `__dirname` to lean on.
const here = dirname(fileURLToPath(import.meta.url));

/**
 * The filename the Python package serves as `TRAIL_MAP_RESOURCE_URI`.
 *
 * Versioned in the name rather than in a query string: the resource URI is the
 * component's contract, and a host that cached the old document should be
 * asking for a different file, not the same one with a different meaning.
 */
const ASSET_NAME = "trail-map-v1.html";

/** Where the built document lands, inside the Python package. */
const ASSET_DIR = resolve(here, "../src/ihm_mcp/assets");

/**
 * Rename Vite's `index.html` to the versioned asset the server registers.
 *
 * Vite names an HTML output after its entry, and the entry has to be
 * `index.html` for `vite dev` to serve it at the root. Renaming at the bundle
 * stage keeps one entry point for development and one filename for the package.
 */
function emitAsVersionedAsset(): Plugin {
  return {
    name: "emit-as-versioned-asset",
    enforce: "post",
    generateBundle(_options, bundle) {
      const built = bundle["index.html"];
      if (!built) {
        this.error("expected an index.html in the bundle to rename");
        return;
      }
      delete bundle["index.html"];
      built.fileName = ASSET_NAME;
      bundle[ASSET_NAME] = built;
    },
  };
}

export default defineConfig({
  // Everything ships inside one document, so nothing may be requested by URL.
  base: "",
  plugins: [viteSingleFile(), emitAsVersionedAsset()],
  build: {
    target: "es2020",
    outDir: ASSET_DIR,
    // The assets directory is package source, not a build scratch directory.
    emptyOutDir: false,
    cssCodeSplit: false,
    // Inline every asset Leaflet's stylesheet references, so the document needs
    // no image origin in its CSP.
    assetsInlineLimit: Number.MAX_SAFE_INTEGER,
    rollupOptions: {
      input: resolve(here, "index.html"),
    },
  },
  test: {
    // jsdom rather than node: the tests import the composition root, which
    // pulls in Leaflet, and Leaflet reads `document` as it loads. Nothing here
    // exercises Leaflet itself — the wiring test uses a fake renderer.
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
  },
});
