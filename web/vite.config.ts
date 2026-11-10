import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "/pothole/",
  plugins: [react()],
  build: { chunkSizeWarningLimit: 1200 },
});
