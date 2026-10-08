"use client";

import { useRef, useState, type DragEvent } from "react";
import { FileText, FileSpreadsheet, FileImage, Presentation, File as FileIcon, UploadCloud, X } from "lucide-react";
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
  onRemoveFile?: (index: number) => void;
  disabled?: boolean;
}

export function fileKind(name: string): { label: string; Icon: typeof FileText; cls: string } {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  if (ext === "pdf") return { label: "PDF", Icon: FileText, cls: "bg-red-50 text-red-600" };
  if (ext === "docx") return { label: "DOCX", Icon: FileText, cls: "bg-blue-50 text-blue-700" };
  if (ext === "pptx") return { label: "PPTX", Icon: Presentation, cls: "bg-amber-50 text-amber-700" };
  if (ext === "xlsx") return { label: "XLSX", Icon: FileSpreadsheet, cls: "bg-green-50 text-green-700" };
  if (["png", "jpg", "jpeg"].includes(ext)) return { label: ext.toUpperCase(), Icon: FileImage, cls: "bg-blue-50 text-blue-700" };
  return { label: ext.toUpperCase() || "FILE", Icon: FileIcon, cls: "bg-gray-100 text-gray-600" };
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function FileDropzone({
  files,
  onFilesSelected,
  onRemoveFile,
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
    <div className="flex flex-col gap-3">
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
        className={`flex flex-col items-center gap-3 rounded-xl border-2 border-dashed bg-white p-8 text-center transition-all duration-200 sm:p-10 ${
          isDragging
            ? "scale-[1.01] border-blue-600 bg-blue-50 shadow-lift"
            : "border-gray-300 hover:border-blue-400 hover:bg-gray-50"
        }`}
      >
        <span
          className={`flex h-14 w-14 items-center justify-center rounded-full transition-colors ${
            isDragging ? "bg-blue-600 text-white" : "bg-blue-50 text-blue-600"
          }`}
          aria-hidden
        >
          <UploadCloud size={28} strokeWidth={1.8} />
        </span>
        <div>
          <p className="font-display text-lg font-semibold text-gray-900">
            {isDragging ? "Drop to add files" : "Drag and drop documents here"}
          </p>
          <p className="mt-0.5 text-sm text-gray-500">or choose files from your computer. Multiple files are processed independently.</p>
        </div>
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={busy}
          className="rounded-full bg-blue-600 px-6 py-2.5 text-sm font-medium text-white shadow-card transition-colors hover:bg-blue-700 disabled:opacity-50"
        >
          {isExtracting ? "Extracting ZIP…" : "Choose files"}
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
        <ul className="flex flex-wrap justify-center gap-1.5" aria-label="Supported formats">
          {["PDF", "DOCX", "PPTX", "XLSX", "JPG", "PNG", "ZIP"].map((f) => {
            const k = fileKind(`x.${f.toLowerCase()}`);
            return (
              <li key={f} className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${k.cls}`}>
                {f}
              </li>
            );
          })}
        </ul>
        <p className="text-xs text-gray-500">
          Up to {MAX_BYTES / (1024 * 1024)} MB per file · ZIP archives up to {MAX_ZIP_BYTES / (1024 * 1024)} MB are unpacked automatically
        </p>
        {rejection && (
          <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
            {rejection}
          </p>
        )}
        {notice && <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">{notice}</p>}
      </div>

      {files.length > 0 && (
        <ul className="grid gap-2 sm:grid-cols-2" aria-label="Selected files">
          {files.map((f, i) => {
            const k = fileKind(f.name);
            return (
              <li key={`${f.name}-${i}`} className="ax-rise flex items-center gap-3 rounded-lg border border-gray-200 bg-white p-3 shadow-card">
                <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${k.cls}`} aria-hidden>
                  <k.Icon size={20} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-gray-900" title={f.name}>{f.name}</span>
                  <span className="block text-xs text-gray-500">{k.label} · {formatSize(f.size)}</span>
                </span>
                {onRemoveFile && (
                  <button
                    type="button"
                    onClick={() => onRemoveFile(i)}
                    disabled={busy}
                    aria-label={`Remove ${f.name}`}
                    className="rounded-full p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-800 disabled:opacity-40"
                  >
                    <X size={16} />
                  </button>
                )}
              </li>
            );
          })}
        </ul>
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
