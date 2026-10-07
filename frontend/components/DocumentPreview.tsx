"use client";

import { useState } from "react";

interface DocumentPreviewProps {
  file: File | null;
  documentId?: string;
}

export default function DocumentPreview({ file, documentId }: DocumentPreviewProps) {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dpi, setDpi] = useState(200);

  const generatePreview = async () => {
    if (!file) return;

    setIsLoading(true);
    setError(null);

    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("page", "0");
      formData.append("dpi", dpi.toString());

      const response = await fetch("http://127.0.0.1:8000/api/preview", {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || "Failed to generate preview");
      }

      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      setPreviewUrl(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to generate preview");
    } finally {
      setIsLoading(false);
    }
  };

  // Auto-generate preview when file changes
  if (file && !previewUrl && !isLoading) {
    generatePreview();
  }

  // Cleanup preview URL when file changes
  if (previewUrl && (!file || documentId)) {
    URL.revokeObjectURL(previewUrl);
    setPreviewUrl(null);
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Document Preview</h3>
        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-500">Quality:</label>
          <select
            value={dpi}
            onChange={(e) => {
              setDpi(Number(e.target.value));
              if (file) generatePreview();
            }}
            className="rounded border border-gray-300 px-2 py-1 text-xs"
          >
            <option value={100}>Low (100 DPI)</option>
            <option value={150}>Medium (150 DPI)</option>
            <option value={200}>High (200 DPI)</option>
            <option value={300}>Ultra (300 DPI)</option>
          </select>
        </div>
      </div>

      {isLoading && (
        <div className="flex h-64 items-center justify-center rounded-lg border border-gray-200 bg-gray-50">
          <p className="text-sm text-gray-500">Generating preview...</p>
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
      )}

      {previewUrl && (
        <div className="rounded-lg border border-gray-200 overflow-hidden">
          <img
            src={previewUrl}
            alt="Document preview"
            className="w-full h-auto"
            style={{ maxHeight: "600px", objectFit: "contain" }}
          />
        </div>
      )}

      {!file && !isLoading && (
        <div className="flex h-64 items-center justify-center rounded-lg border border-gray-200 bg-gray-50">
          <p className="text-sm text-gray-500">
            Upload a document to see preview
          </p>
        </div>
      )}
    </div>
  );
}
