import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs separately (uvicorn on :8077); proxy API paths to it so the
// browser sees one origin, exactly like production where FastAPI serves this build.
const api = "http://localhost:8077";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/v1": api, "/docs": api, "/openapi.json": api, "/health": api },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 900 },
});
