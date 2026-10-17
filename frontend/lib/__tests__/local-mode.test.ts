import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import LandingPage from "../../components/LandingPage";
import { assertLocalApi } from "../local-mode";
import { localFetch, downloadDocument, listDocuments } from "../api-documents";

test("landing exposes Start Extracting and benchmark navigation, with no account experience", () => {
  const html = renderToStaticMarkup(createElement(LandingPage));
  assert.match(html, /href="\/workspace"[^>]*>Start Extracting|href="\/workspace"[\s\S]*?Start Extracting/);
  assert.match(html, /href="\/benchmark"/);
  assert.doesNotMatch(html, /Sign in|Sign up|Create account|Logout|Sign out|type="password"|<form/i);
  assert.match(html, /Extract &amp; Verify/);
});

test("disabled or non-local API/frontend fails closed", () => {
  const previous = process.env.NEXT_PUBLIC_AXTRACT_LOCAL_NO_AUTH;
  try {
    delete process.env.NEXT_PUBLIC_AXTRACT_LOCAL_NO_AUTH;
    assert.throws(() => assertLocalApi("http://127.0.0.1:8000"), /disabled/);
    process.env.NEXT_PUBLIC_AXTRACT_LOCAL_NO_AUTH = "true";
    assert.doesNotThrow(() => assertLocalApi("http://127.0.0.1:8000", "localhost"));
    assert.doesNotThrow(() => assertLocalApi("http://[::1]:8000", "[::1]"));
    assert.throws(() => assertLocalApi("https://example.com"), /loopback/);
    assert.throws(() => assertLocalApi("http://127.0.0.1:8000", "192.168.0.2"), /loopback/);
  } finally {
    if (previous === undefined) delete process.env.NEXT_PUBLIC_AXTRACT_LOCAL_NO_AUTH;
    else process.env.NEXT_PUBLIC_AXTRACT_LOCAL_NO_AUTH = previous;
  }
});

test("documents, download and benchmark requests need no session or authorization header", async () => {
  const previous = process.env.NEXT_PUBLIC_AXTRACT_LOCAL_NO_AUTH;
  const original = globalThis.fetch;
  process.env.NEXT_PUBLIC_AXTRACT_LOCAL_NO_AUTH = "true";
  const paths: string[] = [];
  globalThis.fetch = async (url, init) => {
    paths.push(String(url));
    assert.equal(new Headers(init?.headers).get("authorization"), null);
    return Response.json({ documents: [] });
  };
  try {
    assert.deepEqual(await listDocuments(), { documents: [] });
    await downloadDocument("local-document");
    assert.equal((await localFetch("/api/benchmark/runs")).status, 200);
    assert.equal(paths.length, 3);
    globalThis.fetch = async () => { throw new TypeError("network unavailable"); };
    await assert.rejects(localFetch("/api/benchmark/runs"), /Could not reach/);
  } finally {
    globalThis.fetch = original;
    if (previous === undefined) delete process.env.NEXT_PUBLIC_AXTRACT_LOCAL_NO_AUTH;
    else process.env.NEXT_PUBLIC_AXTRACT_LOCAL_NO_AUTH = previous;
  }
});

test("account-free workspace, history and benchmark have no login guards", () => {
  for (const page of ["workspace", "history", "benchmark"]) {
    const source = readFileSync(`app/${page}/page.tsx`, "utf8");
    assert.doesNotMatch(source, /useAuth|href="\/login"|router\.(?:push|replace)\("\/login"/);
  }
  assert.match(readFileSync("app/login/page.tsx", "utf8"), /redirect\("\/"\)/);
});
