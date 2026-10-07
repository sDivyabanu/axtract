"use client";

import { useRef, useState, type DragEvent } from "react";

const ACCEPTED_TYPES =
  ".pdf,.docx,.pptx,.xlsx,.jpg,.jpeg,.png,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.presentationml.presentation,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,image/jpeg,image/png";

const ALLOWED_EXTENSIONS = ["pdf", "docx", "pptx", "xlsx", "jpg", "jpeg", "png"];
const MAX_BYTES = 100 * 1024 * 1024; // keep in sync with backend MAX_UPLOAD_BYTES

function validateFile(file: File): string | null {
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (!ALLOWED_EXTENSIONS.includes(ext)) {
    return `".${ext}" files are not supported. Use PDF, DOCX, PPTX, XLSX, JPG or PNG.`;
  }
  if (file.size === 0) return "The selected file is empty.";
  if (file.size > MAX_BYTES) return "The file is larger than the 100 MB limit.";
  return null;
}

interface FileDropzoneProps {
  files: File[];
  onFilesSelected: (files: File[]) => void;
  disabled?: boolean;
}

export default function FileDropzone({
  files,
  onFilesSelected,
  disabled = false,
}: FileDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [rejection, setRejection] = useState<string | null>(null);

  function accept(incoming: FileList | undefined) {
    if (!incoming || incoming.length === 0) return;
    const problems: string[] = [];
    const valid: File[] = [];
    for (const file of Array.from(incoming)) {
      const problem = validateFile(file);
      if (problem) problems.push(`${file.name}: ${problem}`);
      else valid.push(file);
    }
    setRejection(problems.length > 0 ? problems.join(" · ") : null);
    if (valid.length > 0) onFilesSelected(valid);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragging(false);
    if (disabled) return;
    accept(event.dataTransfer.files);
  }

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setIsDragging(true);
      }}
      onDragLeave={(event) => {
        // Ignore leave events fired when moving over child elements.
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setIsDragging(false);
      }}
      onDrop={handleDrop}
      className={`flex flex-col items-center gap-3 rounded-lg border-2 border-dashed p-8 text-center transition-colors ${
        isDragging
          ? "border-blue-500 bg-blue-50"
          : "border-gray-300 hover:border-gray-400"
      }`}
    >
      <div className="text-4xl">📄</div>
      <p className="font-medium">Drag and drop files here</p>
      <p className="text-sm text-gray-500">or</p>
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={disabled}
        className="rounded-md bg-gray-900 px-4 py-2 text-sm text-white transition-colors hover:bg-gray-700 disabled:opacity-50"
      >
        Choose Files
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED_TYPES}
        multiple
        className="hidden"
        disabled={disabled}
        onChange={(event) => {
          accept(event.target.files ?? undefined);
          event.target.value = "";
        }}
      />
      <p className="text-xs text-gray-400">
        PDF · DOCX · PPTX · XLSX · JPG · PNG — multiple files supported
      </p>
      {rejection && (
        <p role="alert" className="text-sm text-red-600">
          {rejection}
        </p>
      )}
      {files.length > 0 && (
        <p className="text-sm">
          Selected: <strong>{files.length}</strong>{" "}
          {files.length === 1 ? "file" : "files"}{" "}
          <span className="text-gray-400">
            ({(files.reduce((sum, f) => sum + f.size, 0) / 1024).toFixed(1)} KB
            total)
          </span>
        </p>
      )}
    </div>
  );
}
