import { API_BASE_URL, ApiError } from "./api";
import { consumeParseStream } from "./stream";
import type { StreamHandlers } from "./stream";
import type { DocumentResponse, ValidationReport } from "./types";
import { assertLocalApi } from "./local-mode";

export async function localFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  assertLocalApi(API_BASE_URL);
  try {
    return await fetch(`${API_BASE_URL}${path}`, {
      ...init,
    });
  } catch {
    throw new ApiError(
      "NETWORK_ERROR",
      `Could not reach the backend at ${API_BASE_URL}. Is FastAPI running?`,
    );
  }
}

export async function handleResponse<T>(response: Response): Promise<T> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError(
      "INVALID_RESPONSE",
      `Unexpected response (HTTP ${response.status}).`,
    );
  }

  if (!response.ok) {
    const body = (typeof payload === "object" && payload !== null ? payload : {}) as {
      error?: { code?: string; message?: string };
      detail?: unknown;
    };
    throw new ApiError(
      body.error?.code ?? `HTTP_${response.status}`,
      body.error?.message ??
        (body.detail !== undefined ? String(body.detail) : `Request failed (HTTP ${response.status}).`),
    );
  }

  return payload as T;
}

export interface DocumentListItem {
  id: string;
  original_filename: string;
  mime_type: string;
  file_size_bytes: number;
  status: string;
  page_count: number | null;
  block_count: number | null;
  processing_time_ms: number | null;
  created_at: string;
  updated_at: string;
}

export interface UploadResult {
  document_id: string;
  version_id: string;
  processing_run_id: string;
  result: DocumentResponse;
}

export async function uploadDocument(file: File): Promise<UploadResult> {
  const body = new FormData();
  body.append("file", file);
  const response = await localFetch("/api/documents", {
    method: "POST",
    body,
  });
  return handleResponse<UploadResult>(response);
}

/** Upload with live progress. Same stored result as uploadDocument(). */
export async function uploadDocumentStream(file: File, handlers: StreamHandlers): Promise<UploadResult> {
  const body = new FormData();
  body.append("file", file);
  let response: Response;
  try {
    response = await localFetch("/api/documents/stream", { method: "POST", body, signal: handlers.signal });
  } catch (err) {
    if (handlers.signal?.aborted) throw new ApiError("STREAM_ABORTED", "Cancelled.");
    throw err;
  }
  return consumeParseStream<UploadResult>(response, handlers);
}

export async function listDocuments(
  limit = 50,
  offset = 0,
): Promise<{ documents: DocumentListItem[] }> {
  const response = await localFetch(
    `/api/documents?limit=${limit}&offset=${offset}`,
  );
  return handleResponse(response);
}

export async function getDocumentResult(
  documentId: string,
): Promise<{ document_id: string; result: DocumentResponse; markdown: string }> {
  const response = await localFetch(`/api/documents/${documentId}/result`);
  return handleResponse(response);
}

export async function getDocumentHistory(
  documentId: string,
): Promise<{ runs: unknown[] }> {
  const response = await localFetch(`/api/documents/${documentId}/history`);
  return handleResponse(response);
}

export async function downloadDocument(documentId: string): Promise<Blob> {
  const response = await localFetch(`/api/documents/${documentId}/download`);
  if (!response.ok) {
    throw new ApiError("DOWNLOAD_FAILED", "Failed to download document.");
  }
  return response.blob();
}

export async function deleteDocument(documentId: string): Promise<void> {
  const response = await localFetch(`/api/documents/${documentId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new ApiError("DELETE_FAILED", "Failed to delete document.");
  }
}

/** Re-read the units Verify flagged with secondary readers. Stores candidates; changes no output. */
export async function escalateValidation(
  documentId: string,
): Promise<{ validation: ValidationReport; notes: string[] }> {
  const response = await localFetch(`/api/documents/${documentId}/verify/escalate`, { method: "POST" });
  return handleResponse(response);
}

/** Explicitly accept escalation candidates. The server keeps the earlier result and saves a new one. */
export async function promoteRecovery(
  documentId: string,
  recoveryIds: string[],
  reason: string,
): Promise<{ result: DocumentResponse }> {
  const response = await localFetch(`/api/documents/${documentId}/verify/promote`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ recovery_ids: recoveryIds, reason }),
  });
  return handleResponse(response);
}
