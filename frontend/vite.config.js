import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// En desarrollo (npm run dev) el navegador habla con /graphql y Vite lo reenvía al gateway.
// En Docker lo hace nginx (ver nginx.conf). Así frontend y API comparten origen: cookies sin CORS.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },   // convención de shadcn/ui
  server: {
    port: 5173,
    proxy: { "/graphql": { target: "http://localhost:8000", changeOrigin: false } },
  },
  test: { environment: "jsdom" },   // npm test (vitest): componentes con un DOM simulado
  build: {
    rollupOptions: {
      output: {
        // Librerías en trozos propios: el navegador las cachea aunque cambie el código de la app.
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (/node_modules\/(react|react-dom|scheduler)\//.test(id)) return "react";
          if (/node_modules\/(@apollo|graphql|@wry|optimism|zen-observable|ts-invariant)/.test(id)) return "apollo";
          if (/node_modules\/(motion|framer-motion|motion-dom|motion-utils)\//.test(id)) return "motion";
          return "vendor";
        },
      },
    },
  },
});
