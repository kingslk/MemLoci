import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        configure(proxy) {
          // Vite 8 的 ProxyServer 类型暂未暴露继承自 EventEmitter 的 on()。
          const emitter = proxy as unknown as {
            on: (
              event: "proxyRes",
              listener: (
                proxyRes: { headers: Record<string, string | string[] | undefined> },
                req: { url?: string },
              ) => void,
            ) => void;
          };
          emitter.on("proxyRes", (proxyRes, req) => {
            if (req.url?.includes("/jobs/stream")) {
              proxyRes.headers["cache-control"] = "no-cache";
              proxyRes.headers["x-accel-buffering"] = "no";
            }
          });
        },
      },
      "/health": "http://localhost:8000",
    },
  },
  preview: {
    host: true,
    port: 5173,
  },
});
