"use client";

import { useRef, useState, type DragEvent } from "react";

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
      className={`flex flex-col items-center gap-3 rounded border-2 border-dashed p-8 text-center ${
        isDragging ? "border-blue-600 bg-blue-50" : "border-gray-400"
      }`}
    >
      <p>Drag and drop a file here</p>
      <p className="text-sm text-gray-600">or</p>
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={disabled}
        className="rounded border border-gray-500 px-4 py-2 disabled:opacity-50"
      >
        Choose File
      </button>
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,application/pdf"
        className="hidden"
        disabled={disabled}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onFileSelected(file);
          // Reset so selecting the same file again still fires onChange.
          event.target.value = "";
        }}
      />
      <p className="text-sm">
        {selectedFile ? (
          <>
            Selected: <strong>{selectedFile.name}</strong>
          </>
        ) : (
          "No file selected"
        )}
      </p>
    </div>
  );
}
