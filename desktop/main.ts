// Deno Desktop entry. `deno desktop` opens a webview window pointed at this
// process's `Deno.serve()` handler (it sets DENO_SERVE_ADDRESS). We:
//   1. spawn the werktools sidecar (loopback :7879),
//   2. serve the built Vite SPA from the embedded dist/,
//   3. reverse-proxy /api/* to the sidecar, injecting the session token so the
//      webview never needs to hold it (mutating POSTs are token-gated).

import { startSidecar } from "./bridge/sidecar.ts";
import { serveDir } from "jsr:@std/http/file-server";

const SIDECAR = "http://127.0.0.1:7879";
const { proc, token } = await startSidecar();

addEventListener("unload", () => {
  try {
    proc.kill();
  } catch {
    // already gone
  }
});

Deno.serve(async (req: Request) => {
  const url = new URL(req.url);

  if (url.pathname.startsWith("/api")) {
    const headers = new Headers(req.headers);
    if (token) headers.set("X-Werk-Token", token);
    const body =
      req.method === "GET" || req.method === "HEAD"
        ? undefined
        : await req.arrayBuffer();
    const upstream = await fetch(SIDECAR + url.pathname + url.search, {
      method: req.method,
      headers,
      body,
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: upstream.headers,
    });
  }

  const res = await serveDir(req, { fsRoot: "dist", quiet: true });
  if (res.status === 404) {
    // SPA fallback: serve index.html for client-side routes
    const index = new Request(new URL("/index.html", url.origin), req);
    return await serveDir(index, { fsRoot: "dist", quiet: true });
  }
  return res;
});
