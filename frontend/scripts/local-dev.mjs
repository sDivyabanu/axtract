import { createServer } from "node:http";
import nextEnv from "@next/env";
import next from "next";
import { bindingKey, isLoopback, validateLocalRequest } from "./local-boundary.mjs";

nextEnv.loadEnvConfig(process.cwd(), true);
if (process.env.AXTRACT_LOCAL_NO_AUTH !== "true") {
  throw new Error("Set AXTRACT_LOCAL_NO_AUTH=true explicitly to start AXTRACT locally.");
}
const host = process.env.AXTRACT_LOCAL_HOST || "127.0.0.1";
if (!isLoopback(host)) throw new Error("AXTRACT_LOCAL_HOST must be 127.0.0.1 or ::1.");
const port = Number(process.env.PORT || 3000);
let handler;
const listener = createServer((req, res) => {
  if (!validateLocalRequest(req, listener)) {
    res.writeHead(403).end("Local requests only.");
  } else if (!handler) {
    res.writeHead(503).end("AXTRACT is starting. Please retry.");
  } else if (req.url?.split("?")[0] === "/login") {
    res.writeHead(307, { Location: "/" }).end();
  } else {
    handler(req, res);
  }
});
// Bind BEFORE loading Next config. Config verifies the actual listener, not an env claim.
await new Promise((resolve, reject) => {
  listener.once("error", reject);
  listener.listen({ host, port, ipv6Only: true }, resolve);
});
globalThis[bindingKey] = listener;
try {
  const app = next({ dev: true, hostname: host, port, httpServer: listener });
  await app.prepare();
  handler = app.getRequestHandler();
  console.log(`AXTRACT ready at http://${host === "::1" ? "[::1]" : host}:${port}`);
} catch (error) {
  listener.close();
  throw error;
}
