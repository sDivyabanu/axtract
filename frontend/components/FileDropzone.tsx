"use client";

import { useRef, useState, type DragEvent } from "react";

const ACCEPTED_TYPES =
  ".pdf,.docx,.pptx,.xlsx,.jpg,.jpeg,.png,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.presentationml.presentation,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,image/jpeg,image/png";

interface FileDropzoneProps {
  selectedFile: File | null;
  onFileSelected: (file: File) => void;
  disabled?: boolean;
}

export default function FileDropzone({
  selectedFile,
  onFileSelected,
  disabled = false,
}: FileDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragging(false);
    if (disabled) return;
    const file = event.dataTransfer.files[0];
    if (file) onFileSelected(file);
  }

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setIsDragging(true);
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={handleDrop}
      className={`flex flex-col items-center gap-3 rounded-lg border-2 border-dashed p-8 text-center transition-colors ${
        isDragging
          ? "border-blue-500 bg-blue-50"
          : "border-gray-300 hover:border-gray-400"
      }`}
    >
      <div className="text-4xl">📄</div>
      <p className="font-medium">Drag and drop a file here</p>
      <p className="text-sm text-gray-500">or</p>
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={disabled}
        className="rounded-md bg-gray-900 px-4 py-2 text-sm text-white transition-colors hover:bg-gray-700 disabled:opacity-50"
      >
        Choose File
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED_TYPES}
        className="hidden"
        disabled={disabled}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onFileSelected(file);
          event.target.value = "";
        }}
      />
      <p className="text-xs text-gray-400">
        PDF · DOCX · PPTX · XLSX · JPG · PNG
      </p>
      {selectedFile && (
        <p className="text-sm">
          Selected: <strong>{selectedFile.name}</strong>{" "}
          <span className="text-gray-400">
            ({(selectedFile.size / 1024).toFixed(1)} KB)
          </span>
        </p>
      )}
    </div>
  );
}
