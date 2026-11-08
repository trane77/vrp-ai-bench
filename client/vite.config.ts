import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: { "/api": { target: "http://127.0.0.1:8000", ws: true } },
  },
  build: {
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/maplibre-gl")) return "vendor-maplibre";
          if (id.includes("node_modules/@deck.gl") || id.includes("node_modules/@luma.gl") || id.includes("node_modules/@loaders.gl") || id.includes("node_modules/@math.gl")) {
            return "vendor-deckgl";
          }
        },
      },
    },
  },
});
