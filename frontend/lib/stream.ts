import { API_BASE_URL, ApiError } from "./api";
import type { DocumentResponse } from "./types";
import { applyStage, applyStart, finish, initialPipeline } from "./pipeline";
import type { PipelineState, StageEvent, StageInfo } from "./pipeline";
import { readSse } from "./sse";

export interface StreamHandlers {
  /** Receives an updater for the tab's pipeline state after every real event from the server. */
  onPipeline: (update: (prev: PipelineState) => PipelineState) => void;
  signal?: AbortSignal;
}

// Endpoints that do not exist on an older backend: the caller falls back to the plain request.
const NO_STREAMING = new Set([404, 405, 501]);

/**
 * Read a streaming parse response and resolve with the server's `result` payload.
 * Throws ApiError STREAM_UNAVAILABLE (nothing was processed: use the plain endpoint), the server's own
 * error, STREAM_LOST (the connection ended before a result, so the outcome is unknown) or STREAM_ABORTED.
 */
export async function consumeParseStream<T>(response: Response, handlers: StreamHandlers): Promise<T> {
  if (NO_STREAMING.has(response.status)) {
    throw new ApiError("STREAM_UNAVAILABLE", "Live progress is not available on this server.");
  }
  const type = response.headers.get("content-type") ?? "";
  if (!type.includes("text/event-stream") || !response.body) {
    // a plain JSON error from validation, authentication or a proxy, before any stream started
    let message = `Request failed (HTTP ${response.status}).`;
    let code = `HTTP_${response.status}`;
    try {
      const body = await response.json();
      code = body?.error?.code ?? code;
      message = body?.error?.message ?? (body?.detail !== undefined ? String(body.detail) : message);
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(code, message);
  }

  handlers.onPipeline(() => initialPipeline());
  let result: T | undefined;
  let failure: ApiError | null = null;
  try {
    await readSse(response.body, ({ event, data }) => {
      let payload: unknown;
      try {
        payload = JSON.parse(data);
      } catch {
        return;
      }
      if (event === "start") {
        const p = payload as { stages: StageInfo[]; verify: boolean };
        handlers.onPipeline((prev) => applyStart(prev, p.stages, p.verify));
      } else if (event === "stage") {
        handlers.onPipeline((prev) => applyStage(prev, payload as StageEvent));
      } else if (event === "result") {
        result = payload as T;
      } else if (event === "error") {
        const e = payload as { code?: string; message?: string };
        failure = new ApiError(e.code ?? "PROCESSING_FAILED", e.message ?? "Processing failed.");
      }
    });
  } catch {
    if (handlers.signal?.aborted) {
      handlers.onPipeline((prev) => finish(prev, "error"));
      throw new ApiError("STREAM_ABORTED", "Cancelled.");
    }
    // otherwise the connection dropped mid-stream; handled below as STREAM_LOST
  }
  if (result !== undefined) {
    handlers.onPipeline((prev) => finish(prev, "done"));
    return result;
  }
  handlers.onPipeline((prev) => finish(prev, "error"));
  if (failure) throw failure;
  throw new ApiError("STREAM_LOST", "The connection was lost before the result arrived. Please try again.");
}

/** Anonymous parse with live progress. Same result as parseDocument(). */
export async function parseDocumentStream(file: File, handlers: StreamHandlers): Promise<DocumentResponse> {
  const body = new FormData();
  body.append("file", file);
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/parse/stream`, { method: "POST", body, signal: handlers.signal });
  } catch {
    if (handlers.signal?.aborted) throw new ApiError("STREAM_ABORTED", "Cancelled.");
    throw new ApiError("NETWORK_ERROR", `Could not reach the backend at ${API_BASE_URL}. Is FastAPI running?`);
  }
  return consumeParseStream<DocumentResponse>(response, handlers);
}
