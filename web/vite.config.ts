import { readFileSync } from "node:fs";

import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

// maplibre-gl 6 loads its worker as a separate module that imports a shared chunk next to it.
// The bundler does not follow that, so both files are copied into the build as they are.
const WORKER_FILES = ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"];
function maplibreWorker(): Plugin {
  return {
    name: "maplibre-worker",
    apply: "build",
    generateBundle() {
      for (const name of WORKER_FILES) {
        this.emitFile({
          type: "asset",
          fileName: `maplibre/${name}`,
          source: readFileSync(new URL(`./node_modules/maplibre-gl/dist/${name}`, import.meta.url)),
        });
      }
    },
  };
}

export default defineConfig({
  base: "/pothole/",
  plugins: [react(), maplibreWorker()],
  build: { chunkSizeWarningLimit: 1200 },
});
