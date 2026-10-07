"use client";

import { useRef, useState, type DragEvent } from "react";
import { extractZip } from "@/lib/unzip";

const ACCEPTED_TYPES =
  ".pdf,.docx,.pptx,.xlsx,.jpg,.jpeg,.png,.zip,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.presentationml.presentation,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,image/jpeg,image/png,application/zip";

const ALLOWED_EXTENSIONS = ["pdf", "docx", "pptx", "xlsx", "jpg", "jpeg", "png", "zip"];
const MAX_BYTES = 100 * 1024 * 1024; // keep in sync with backend MAX_UPLOAD_BYTES
const MAX_ZIP_BYTES = 300 * 1024 * 1024;

function validateFile(file: File): string | null {
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (!ALLOWED_EXTENSIONS.includes(ext)) {
    return `".${ext}" files are not supported. Use PDF, DOCX, PPTX, XLSX, JPG, PNG or ZIP.`;
  }
  if (file.size === 0) return "The selected file is empty.";
  const limit = ext === "zip" ? MAX_ZIP_BYTES : MAX_BYTES;
  if (file.size > limit) {
    return `The file is larger than the ${limit / (1024 * 1024)} MB limit.`;
  }
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
  const [notice, setNotice] = useState<string | null>(null);
  const [isExtracting, setIsExtracting] = useState(false);

  async function accept(incoming: FileList | undefined) {
    if (!incoming || incoming.length === 0) return;
    const busy = disabled || isExtracting;
    if (busy) return;

    const problems: string[] = [];
    const valid: File[] = [];
    const zips: File[] = [];

    for (const file of Array.from(incoming)) {
      const problem = validateFile(file);
      if (problem) {
        problems.push(`${file.name}: ${problem}`);
        continue;
      }
      const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
      if (ext === "zip") zips.push(file);
      else valid.push(file);
    }

    setRejection(problems.length > 0 ? problems.join(" · ") : null);
    setNotice(null);

    if (zips.length > 0) {
      setIsExtracting(true);
      try {
        const accepted: File[] = [];
        const allSkipped: string[] = [];
        for (const zip of zips) {
          try {
            const { files: members, skipped } = await extractZip(zip);
            accepted.push(...members);
            allSkipped.push(...skipped);
          } catch (err) {
            problems.push(
              `${zip.name}: ${err instanceof Error ? err.message : "could not be read"}`,
            );
          }
        }
        if (allSkipped.length > 0) {
          setNotice(
            `ZIP contents: ${accepted.length} file(s) accepted, ${allSkipped.length} skipped — ${allSkipped.slice(0, 5).join(" · ")}${allSkipped.length > 5 ? " …" : ""}`,
          );
        } else if (accepted.length > 0) {
          setNotice(`ZIP contents: ${accepted.length} file(s) accepted.`);
        }
        if (accepted.length > 0) onFilesSelected(accepted);
        if (problems.length > 0) setRejection(problems.join(" · "));
      } finally {
        setIsExtracting(false);
      }
      return;
    }

    if (valid.length > 0) onFilesSelected(valid);
  }

  const busy = disabled || isExtracting;

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        if (!busy) setIsDragging(true);
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
        disabled={busy}
        className="rounded-md bg-gray-900 px-4 py-2 text-sm text-white transition-colors hover:bg-gray-700 disabled:opacity-50"
      >
        {isExtracting ? "Extracting ZIP…" : "Choose Files"}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED_TYPES}
        multiple
        className="hidden"
        disabled={busy}
        onChange={(event) => {
          accept(event.target.files ?? undefined);
          event.target.value = "";
        }}
      />
      <p className="text-xs text-gray-400">
        PDF · DOCX · PPTX · XLSX · JPG · PNG · ZIP (contents are extracted
        automatically)
      </p>
      {rejection && (
        <p role="alert" className="text-sm text-red-600">
          {rejection}
        </p>
      )}
      {notice && (
        <p className="text-xs text-amber-700">{notice}</p>
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

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragging(false);
    if (busy) return;
    accept(event.dataTransfer.files);
  }
}
