import type { DocumentResponse, ErrorResponse } from "./types";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.code = code;
  }
}

function isErrorResponse(payload: unknown): payload is ErrorResponse {
  return (
    typeof payload === "object" &&
    payload !== null &&
    "status" in payload &&
    (payload as { status: unknown }).status === "error"
  );
}

export async function parseDocument(file: File): Promise<DocumentResponse> {
  const body = new FormData();
  body.append("file", file);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/parse`, {
      method: "POST",
      body,
    });
  } catch {
    throw new ApiError(
      "NETWORK_ERROR",
      `Could not reach the backend at ${API_BASE_URL}. Is FastAPI running?`,
    );
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError(
      "INVALID_RESPONSE",
      `The backend returned an unexpected response (HTTP ${response.status}).`,
    );
  }

  if (!response.ok || isErrorResponse(payload)) {
    if (isErrorResponse(payload)) {
      throw new ApiError(payload.error.code, payload.error.message);
    }
    throw new ApiError("HTTP_ERROR", `Request failed with HTTP ${response.status}.`);
  }

  return payload as DocumentResponse;
}
