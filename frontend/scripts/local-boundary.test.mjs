import assert from "node:assert/strict";
import { createServer } from "node:http";
import { test } from "node:test";
import { isLoopback, validateLocalRequest, verifyLocalBinding } from "./local-boundary.mjs";

test("only literal loopback bind addresses are accepted", () => {
  for (const host of ["127.0.0.1", "::1"]) assert.equal(isLoopback(host), true);
  for (const host of ["0.0.0.0", "::", "192.168.0.2", "localhost", "example.com"]) assert.equal(isLoopback(host), false);
});

test("flag or claimed hostname is insufficient; verify the real listening socket", async () => {
  assert.throws(() => verifyLocalBinding(undefined));
  for (const host of ["0.0.0.0", "127.0.0.1"]) {
    const server = createServer();
    await new Promise((resolve) => server.listen(0, host, resolve));
    try {
      if (host === "0.0.0.0") assert.throws(() => verifyLocalBinding(server));
      else assert.doesNotThrow(() => verifyLocalBinding(server));
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  }
});

test("reject rebinding, external origins, proxy claims and remote peers", async () => {
  const server = createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const req = (headers = {}, peer = "127.0.0.1") => ({ socket: { remoteAddress: peer }, headers: { host: "127.0.0.1:3000", ...headers } });
  try {
    assert.equal(validateLocalRequest(req(), server), true);
    assert.equal(validateLocalRequest(req({ origin: "http://localhost:3000" }), server), true);
    for (const headers of [{ host: "attacker.example" }, { origin: "https://attacker.example" }, { origin: "null" }, { "x-forwarded-for": "127.0.0.1" }, { "sec-fetch-site": "cross-site" }]) {
      assert.equal(validateLocalRequest(req(headers), server), false);
    }
    assert.equal(validateLocalRequest(req({}, "192.168.0.2"), server), false);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
