/**
 * Client-side ZIP extraction for the upload flow.
 *
 * The ZIP itself is never uploaded: members are extracted in the browser and
 * only the accepted ones are handed to the normal upload path, so the archive
 * never reaches the backend or the database — each extracted file gets its own
 * document entry, tab, and history record.
 */

import { unzip } from "fflate";

const ALLOWED_EXTENSIONS = [
  "pdf", "docx", "doc", "pptx", "ppt", "xlsx", "xls", "csv",
  "jpg", "jpeg", "png", "tif", "tiff", "heic",
  "txt", "md", "html", "htm", "rtf", "eml", "msg",
];
const MAX_FILE_BYTES = 100 * 1024 * 1024; // keep in sync with backend MAX_UPLOAD_BYTES
const MAX_FILES_PER_ZIP = 50;
const MAX_TOTAL_UNCOMPRESSED = 300 * 1024 * 1024;
const MAX_COMPRESSION_RATIO = 200; // zip-bomb guard: expansion beyond 200x is rejected

const MIME: Record<string, string> = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  doc: "application/msword",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ppt: "application/vnd.ms-powerpoint",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  xls: "application/vnd.ms-excel",
  csv: "text/csv",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  tif: "image/tiff",
  tiff: "image/tiff",
  heic: "image/heic",
  txt: "text/plain",
  md: "text/plain",
  html: "text/html",
  htm: "text/html",
  rtf: "application/rtf",
  eml: "message/rfc822",
  msg: "application/vnd.ms-outlook",
};

// System junk that should disappear silently (macOS/Windows artefacts).
const JUNK = /(^|\/)(__MACOSX|\.DS_Store|Thumbs\.db)$/i;

export interface ZipExtraction {
  files: File[];
  skipped: string[]; // human-readable: "<path>: <reason>"
}

export async function extractZip(zipFile: File): Promise<ZipExtraction> {
  if (zipFile.size > MAX_TOTAL_UNCOMPRESSED) {
    throw new Error(
      `ZIP is larger than the ${MAX_TOTAL_UNCOMPRESSED / (1024 * 1024)} MB limit.`,
    );
  }

  const data = new Uint8Array(await zipFile.arrayBuffer());
  const entries = await new Promise<Record<string, Uint8Array>>(
    (resolve, reject) => {
      unzip(data, (err, unzipped) => (err ? reject(err) : resolve(unzipped)));
    },
  );

  let uncompressed = 0;
  for (const bytes of Object.values(entries)) uncompressed += bytes.length;
  if (zipFile.size > 0 && uncompressed / zipFile.size > MAX_COMPRESSION_RATIO) {
    throw new Error(
      "ZIP expands to more than 200× its size — rejected as a possible decompression bomb.",
    );
  }

  const files: File[] = [];
  const skipped: string[] = [];
  let acceptedBytes = 0;

  for (const [path, bytes] of Object.entries(entries)) {
    if (path.endsWith("/")) continue; // bare folder entry
    const base = path.split("/").pop() ?? path;
    // macOS/Windows junk and hidden files vanish silently.
    if (JUNK.test(path) || base.startsWith("._") || base.startsWith(".")) continue;

    const ext = base.split(".").pop()?.toLowerCase() ?? "";
    if (ext === "zip") {
      skipped.push(`${path}: nested archive (not supported)`);
      continue;
    }
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      skipped.push(`${path}: unsupported format`);
      continue;
    }
    if (bytes.length === 0) {
      skipped.push(`${path}: empty file`);
      continue;
    }
    if (bytes.length > MAX_FILE_BYTES) {
      skipped.push(`${path}: larger than 100 MB`);
      continue;
    }
    if (acceptedBytes + bytes.length > MAX_TOTAL_UNCOMPRESSED) {
      skipped.push(`${path}: unpacked total exceeds 300 MB`);
      continue;
    }
    if (files.length >= MAX_FILES_PER_ZIP) {
      skipped.push(`${path}: limit of ${MAX_FILES_PER_ZIP} files per ZIP reached`);
      continue;
    }

    acceptedBytes += bytes.length;
    // Keep the relative path as the file name so same-named files in different
    // folders stay distinguishable in tabs and history.
    const copy = new Uint8Array(bytes); // satisfy BlobPart typing for File()
    files.push(new File([copy], path, { type: MIME[ext] ?? "application/octet-stream" }));
  }

  return { files, skipped };
}
