/** No token or account fallback. Both servers must explicitly opt into loopback mode. */
export function assertLocalApi(baseUrl: string, frontendHost?: string): void {
  if (process.env.NEXT_PUBLIC_AXTRACT_LOCAL_NO_AUTH !== "true") {
    throw new Error("Local development mode is disabled. Use the dev:local launcher.");
  }
  const url = new URL(baseUrl);
  const host = url.hostname;
  const local = ["127.0.0.1", "[::1]", "localhost"];
  if (!["http:", "https:"].includes(url.protocol) || !local.includes(host)) throw new Error("AXTRACT only connects to a loopback HTTP API.");
  const pageHost = frontendHost ?? (typeof window === "undefined" ? undefined : window.location.hostname);
  if (pageHost && !local.includes(pageHost)) throw new Error("AXTRACT only runs on loopback.");
}
