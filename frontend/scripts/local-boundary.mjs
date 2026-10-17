import { isIP } from "node:net";

export const bindingKey = Symbol.for("axtract.local-listener");
export const isLoopback = (host) => isIP(host) !== 0 && ["127.0.0.1", "::1"].includes(host);

export function verifyLocalBinding(listener) {
  const address = listener?.address();
  if (!address || typeof address === "string" || !isLoopback(address.address)) {
    throw new Error("AXTRACT requires the loopback-bound dev:local launcher.");
  }
}

export function validateLocalRequest(req, listener) {
  verifyLocalBinding(listener);
  if (!isLoopback(req.socket.remoteAddress)) return false;
  if (["forwarded", "x-forwarded-for", "x-forwarded-host"].some((h) => req.headers[h])) return false;
  try {
    const host = new URL(`http://${req.headers.host}`).hostname;
    if (!["127.0.0.1", "[::1]", "localhost"].includes(host)) return false;
    if (req.headers.origin) {
      const origin = new URL(req.headers.origin);
      if (!["http:", "https:"].includes(origin.protocol) || !["127.0.0.1", "[::1]", "localhost"].includes(origin.hostname)) return false;
    }
    return req.headers["sec-fetch-site"] !== "cross-site";
  } catch {
    return false;
  }
}
