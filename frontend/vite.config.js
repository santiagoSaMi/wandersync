import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// En desarrollo (npm run dev) el navegador habla con /graphql y Vite lo reenvía al gateway.
// En Docker lo hace nginx (ver nginx.conf). Así frontend y API comparten origen: cookies sin CORS.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/graphql": { target: "http://localhost:8000", changeOrigin: false } },
  },
  test: { environment: "jsdom" },   // npm test (vitest): componentes con un DOM simulado
});
