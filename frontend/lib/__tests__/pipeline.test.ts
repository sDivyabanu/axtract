import assert from "node:assert/strict";
import test from "node:test";
import { ApiError } from "../api";
import {
  applyStage,
  applyStart,
  currentStageId,
  describeOutcome,
  finish,
  initialPipeline,
  settledCount,
  type StageEvent,
} from "../pipeline";
import { SseParser } from "../sse";
import { consumeParseStream } from "../stream";
import type { PipelineState } from "../pipeline";

const ev = (id: string, state: StageEvent["state"], seq: number, detail?: StageEvent["detail"]): StageEvent => ({
  id,
  state,
  seq,
  elapsed_ms: seq * 100,
  detail,
});

// ------------------------------------------------------------------ pipeline state

test("a new pipeline has every stage pending and nothing is invented", () => {
  const p = initialPipeline();
  assert.equal(p.stages.length, 7);
  assert.ok(p.stages.every((s) => p.progress[s.id].state === "pending"));
  assert.equal(settledCount(p), 0);
  assert.equal(currentStageId(p), null);
});

test("events change only the stage they name, in server order", () => {
  let p = initialPipeline();
  p = applyStage(p, ev("security", "running", 1));
  assert.equal(currentStageId(p), "security");
  p = applyStage(p, ev("security", "completed", 2, { findings: 0 }));
  p = applyStage(p, ev("extraction", "running", 3));
  assert.equal(p.progress.security.state, "completed");
  assert.equal(p.progress.extraction.state, "running");
  assert.equal(p.progress.content.state, "pending");
  assert.equal(settledCount(p), 1);
});

test("stale, duplicate and unknown events are ignored", () => {
  let p = applyStage(initialPipeline(), ev("security", "completed", 5));
  const same = applyStage(p, ev("security", "running", 4));
  assert.equal(same, p);
  assert.equal(applyStage(p, ev("security", "failed", 5)), p);
  assert.equal(applyStage(p, ev("nonsense", "completed", 9)), p);
});

test("the server's own stage list replaces the defaults", () => {
  const p = applyStart(initialPipeline(), [{ id: "a", label: "A", description: "d" }], false);
  assert.deepEqual(Object.keys(p.progress), ["a"]);
  assert.equal(p.verifyEnabled, false);
});

test("a stage still running when the stream dies is shown as failed, never completed", () => {
  let p = applyStage(initialPipeline(), ev("security", "completed", 1));
  p = applyStage(p, ev("extraction", "running", 2));
  const lost = finish(p, "error");
  assert.equal(lost.progress.extraction.state, "failed");
  assert.equal(lost.progress.security.state, "completed");
  assert.equal(lost.phase, "error");
  assert.equal(finish(p, "done").progress.extraction.state, "running");
});

test("outcome text is built only from reported counts", () => {
  const s = (state: StageEvent["state"], detail?: StageEvent["detail"]) => ({ state, detail: detail ?? null, elapsedMs: 1 });
  assert.equal(describeOutcome("security", s("completed", { findings: 0 })), "No security findings");
  assert.equal(describeOutcome("security", s("warning", { findings: 2 })), "2 security findings");
  assert.equal(describeOutcome("extraction", s("completed", { blocks: 10, pages: 1 })), "10 blocks · 1 page");
  assert.equal(describeOutcome("content", s("warning", { issues: 3 })), "3 issues found");
  assert.equal(describeOutcome("verdict", s("warning", { status: "review_required" })), "review required");
  assert.match(describeOutcome("inventory", s("skipped", { reason: "disabled" })), /turned off/);
  assert.match(describeOutcome("content", s("failed", { error_type: "ValueError" })), /ValueError/);
});

// ------------------------------------------------------------------ SSE parsing

test("SSE messages split across chunks are reassembled and comments are dropped", () => {
  const parser = new SseParser();
  const a = parser.push('event: stage\ndata: {"id":"sec');
  assert.deepEqual(a, []);
  const b = parser.push('urity"}\n\n: ping\n\nevent: result\r\ndata: {"ok":1}\r\n\r\n');
  assert.deepEqual(b, [
    { event: "stage", data: '{"id":"security"}' },
    { event: "result", data: '{"ok":1}' },
  ]);
});

// ------------------------------------------------------------------ stream consumption

function sseResponse(chunks: string[], init?: { fail?: boolean }): Response {
  const enc = new TextEncoder();
  let sent = false;
  const body = new ReadableStream<Uint8Array>({
    pull(c) {
      if (!sent) {
        sent = true;
        for (const ch of chunks) c.enqueue(enc.encode(ch));
      } else if (init?.fail) c.error(new Error("socket reset"));
      else c.close();
    },
  });
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
}

const msg = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

function collector() {
  let state: PipelineState = initialPipeline();
  return { handlers: { onPipeline: (u: (p: PipelineState) => PipelineState) => (state = u(state)) }, get: () => state };
}

test("a complete stream yields the result and a finished pipeline", async () => {
  const c = collector();
  const res = await consumeParseStream<{ id: string }>(
    sseResponse([
      msg("start", { stages: initialPipeline().stages, verify: true }),
      msg("stage", ev("security", "running", 1)),
      msg("stage", ev("security", "completed", 2, { findings: 0 })),
      msg("result", { id: "doc" }),
    ]),
    c.handlers,
  );
  assert.deepEqual(res, { id: "doc" });
  assert.equal(c.get().phase, "done");
  assert.equal(c.get().progress.security.state, "completed");
});

test("a server error event surfaces the server's code and marks the pipeline stopped", async () => {
  const c = collector();
  await assert.rejects(
    consumeParseStream(sseResponse([msg("stage", ev("security", "running", 1)), msg("error", { code: "INVALID_FILE", message: "bad" })]), c.handlers),
    (e: unknown) => e instanceof ApiError && e.code === "INVALID_FILE",
  );
  assert.equal(c.get().phase, "error");
  assert.equal(c.get().progress.security.state, "failed");
});

test("a connection lost mid-stream is STREAM_LOST, not a fake success", async () => {
  const c = collector();
  await assert.rejects(
    consumeParseStream(sseResponse([msg("stage", ev("extraction", "running", 1))], { fail: true }), c.handlers),
    (e: unknown) => e instanceof ApiError && e.code === "STREAM_LOST",
  );
  assert.equal(c.get().progress.extraction.state, "failed");
});

test("a stream that just ends without a result is STREAM_LOST", async () => {
  await assert.rejects(
    consumeParseStream(sseResponse([msg("stage", ev("security", "running", 1))]), collector().handlers),
    (e: unknown) => e instanceof ApiError && e.code === "STREAM_LOST",
  );
});

test("a missing streaming endpoint asks the caller to fall back to the plain request", async () => {
  for (const status of [404, 405, 501]) {
    await assert.rejects(
      consumeParseStream(new Response("nope", { status }), collector().handlers),
      (e: unknown) => e instanceof ApiError && e.code === "STREAM_UNAVAILABLE",
    );
  }
});

test("a JSON error before the stream starts keeps the server's code and message", async () => {
  const res = new Response(JSON.stringify({ status: "error", error: { code: "AUTH_REQUIRED", message: "Sign in" } }), {
    status: 401,
    headers: { "content-type": "application/json" },
  });
  await assert.rejects(
    consumeParseStream(res, collector().handlers),
    (e: unknown) => e instanceof ApiError && e.code === "AUTH_REQUIRED" && e.message === "Sign in",
  );
});

test("aborting the request reports STREAM_ABORTED", async () => {
  const controller = new AbortController();
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller.signal.addEventListener("abort", () => c.error(new DOMException("aborted", "AbortError")));
    },
  });
  const res = new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
  const pending = consumeParseStream(res, { ...collector().handlers, signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, (e: unknown) => e instanceof ApiError && e.code === "STREAM_ABORTED");
});

test("two files keep independent pipeline state", () => {
  let a = initialPipeline();
  let b = initialPipeline();
  a = applyStage(a, ev("security", "running", 1));
  b = applyStage(b, ev("security", "completed", 1));
  b = applyStage(b, ev("extraction", "failed", 2));
  assert.equal(a.progress.security.state, "running");
  assert.equal(a.progress.extraction.state, "pending");
  assert.equal(b.progress.extraction.state, "failed");
});
