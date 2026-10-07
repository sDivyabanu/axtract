import type { BlockType, DocumentBlock } from "./types";

export interface FilterState {
  query: string;
  pageInput: string;
  pages: number[] | null;
  types: Set<BlockType>;
  extractors: Set<string>;
  confidence: ConfidenceFilter;
  review: ReviewFilter;
}

export type ConfidenceFilter = "all" | "high" | "medium" | "low" | "none";
export type ReviewFilter = "all" | "review" | "clean";

export const CONFIDENCE_THRESHOLDS = {
  high: 0.85,
  medium: 0.60,
} as const;

export function getConfidenceCounts(blocks: DocumentBlock[]): Record<ConfidenceFilter, number> {
  const counts: Record<ConfidenceFilter, number> = {
    all: blocks.length,
    high: 0,
    medium: 0,
    low: 0,
    none: 0,
  };
  for (const b of blocks) {
    if (b.confidence === null || b.confidence === undefined) {
      counts.none++;
    } else if (b.confidence >= CONFIDENCE_THRESHOLDS.high) {
      counts.high++;
    } else if (b.confidence >= CONFIDENCE_THRESHOLDS.medium) {
      counts.medium++;
    } else {
      counts.low++;
    }
  }
  return counts;
}

export function createInitialFilters(): FilterState {
  return {
    query: "",
    pageInput: "",
    pages: null,
    types: new Set(),
    extractors: new Set(),
    confidence: "all",
    review: "all",
  };
}

export function hasActiveFilters(f: FilterState): boolean {
  return (
    f.query !== "" ||
    f.pages !== null ||
    f.types.size > 0 ||
    f.extractors.size > 0 ||
    f.confidence !== "all" ||
    f.review !== "all"
  );
}

export function parsePageInput(input: string, maxPage: number): number[] | null {
  const trimmed = input.trim();
  if (!trimmed) return null;

  const pages: Set<number> = new Set();
  const parts = trimmed.split(",");

  for (const part of parts) {
    const rangeParts = part.trim().split("-");
    if (rangeParts.length === 1) {
      const n = parseInt(rangeParts[0], 10);
      if (!isNaN(n) && n >= 1 && n <= maxPage) pages.add(n);
    } else if (rangeParts.length === 2) {
      const start = parseInt(rangeParts[0], 10);
      const end = parseInt(rangeParts[1], 10);
      if (!isNaN(start) && !isNaN(end) && start >= 1 && end <= maxPage && start <= end) {
        for (let i = start; i <= end; i++) pages.add(i);
      }
    }
  }

  return pages.size > 0 ? Array.from(pages).sort((a, b) => a - b) : null;
}

function blockMatchesQuery(block: DocumentBlock, lowerQuery: string): boolean {
  if (block.content.toLowerCase().includes(lowerQuery)) return true;
  if (block.type === "table") {
    const rows = block.metadata?.rows as string[][] | undefined;
    if (rows) {
      for (const row of rows) {
        for (const cell of row) {
          if (String(cell).toLowerCase().includes(lowerQuery)) return true;
        }
      }
    }
  }
  return false;
}

function blockMatchesConfidence(block: DocumentBlock, filter: ConfidenceFilter): boolean {
  const c = block.confidence;
  switch (filter) {
    case "all":
      return true;
    case "high":
      return c !== null && c !== undefined && c >= CONFIDENCE_THRESHOLDS.high;
    case "medium":
      return (
        c !== null &&
        c !== undefined &&
        c >= CONFIDENCE_THRESHOLDS.medium &&
        c < CONFIDENCE_THRESHOLDS.high
      );
    case "low":
      return c !== null && c !== undefined && c < CONFIDENCE_THRESHOLDS.medium;
    case "none":
      return c === null || c === undefined;
  }
}

export function filterBlocks(
  blocks: DocumentBlock[],
  filters: FilterState,
): DocumentBlock[] {
  const lowerQuery = filters.query.toLowerCase();
  const pageSet = filters.pages ? new Set(filters.pages) : null;

  return blocks.filter((block) => {
    if (pageSet && !pageSet.has(block.page)) return false;
    if (filters.types.size > 0 && !filters.types.has(block.type)) return false;
    if (filters.extractors.size > 0 && !filters.extractors.has(block.extractor))
      return false;
    if (!blockMatchesConfidence(block, filters.confidence)) return false;
    if (filters.review === "review" && !block.requires_review) return false;
    if (filters.review === "clean" && block.requires_review) return false;
    if (lowerQuery && !blockMatchesQuery(block, lowerQuery)) return false;
    return true;
  });
}

export function getUniqueExtractors(blocks: DocumentBlock[]): string[] {
  return Array.from(new Set(blocks.map((b) => b.extractor))).sort();
}

export function getPageSummaries(
  blocks: DocumentBlock[],
  maxPage: number,
): Map<number, Set<string>> {
  const map = new Map<number, Set<string>>();
  for (let p = 1; p <= maxPage; p++) map.set(p, new Set());
  for (const block of blocks) {
    const set = map.get(block.page);
    if (set) {
      if (block.type === "table") set.add("Table");
      else if (block.type === "chart") set.add("Chart");
      else if (block.type === "figure") set.add("Figure");
      else if (block.type === "equation") set.add("Equation");
      if (block.confidence !== null) set.add("OCR");
      if (block.requires_review) set.add("Review");
    }
  }
  return map;
}

export function findMatchIndices(
  blocks: DocumentBlock[],
  query: string,
): { blockIndex: number; positions: number[] }[] {
  if (!query) return [];
  const lower = query.toLowerCase();
  const results: { blockIndex: number; positions: number[] }[] = [];

  for (let i = 0; i < blocks.length; i++) {
    const content = blocks[i].content.toLowerCase();
    const positions: number[] = [];
    let pos = content.indexOf(lower);
    while (pos !== -1) {
      positions.push(pos);
      pos = content.indexOf(lower, pos + 1);
    }
    if (blocks[i].type === "table") {
      const rows = blocks[i].metadata?.rows as string[][] | undefined;
      if (rows) {
        for (const row of rows) {
          for (const cell of row) {
            if (String(cell).toLowerCase().includes(lower)) {
              if (positions.length === 0) positions.push(-1);
            }
          }
        }
      }
    }
    if (positions.length > 0) {
      results.push({ blockIndex: i, positions });
    }
  }

  return results;
}

export function formatPageRange(pages: number[]): string {
  if (!pages || pages.length === 0) return "";
  const sorted = [...pages].sort((a, b) => a - b);
  const ranges: string[] = [];
  let start = sorted[0];
  let end = sorted[0];

  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i] === end + 1) {
      end = sorted[i];
    } else {
      ranges.push(start === end ? `${start}` : `${start}-${end}`);
      start = sorted[i];
      end = sorted[i];
    }
  }
  ranges.push(start === end ? `${start}` : `${start}-${end}`);
  return ranges.join(", ");
}

export function generateFilteredMarkdown(blocks: DocumentBlock[]): string {
  if (blocks.length === 0) return "";

  const sorted = [...blocks].sort((a, b) => {
    const ro_a = a.reading_order ?? 999999;
    const ro_b = b.reading_order ?? 999999;
    if (ro_a !== ro_b) return ro_a - ro_b;
    return a.page - b.page;
  });

  const parts: string[] = [];
  let currentPage = -1;

  for (const block of sorted) {
    if (block.page !== currentPage) {
      if (currentPage > 0) {
        parts.push("");
        parts.push("---");
        parts.push("");
      }
      currentPage = block.page;
    }
    const md = blockToMarkdown(block);
    if (md) {
      parts.push(md);
      parts.push("");
    }
  }

  return parts.join("\n").trim() + "\n";
}

function blockToMarkdown(block: DocumentBlock): string {
  const content = block.content.trim();
  if (!content) return "";

  switch (block.type) {
    case "heading": {
      const level = Math.max(1, Math.min(6, (block.metadata?.heading_level as number) ?? 1));
      return `${"#".repeat(level)} ${content}`;
    }
    case "paragraph":
      return content;
    case "list": {
      return content
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean)
        .map((l) => {
          if (l.startsWith("- ") || l.startsWith("* ") || l.startsWith("• "))
            return `- ${l.slice(2)}`;
          return `- ${l}`;
        })
        .join("\n");
    }
    case "table": {
      const rows = block.metadata?.rows as string[][] | undefined;
      if (!rows?.length) return `\`\`\`\n${content}\n\`\`\``;
      const lines: string[] = [];
      for (let i = 0; i < rows.length; i++) {
        const cells = rows[i].map((c) =>
          String(c ?? "").replace(/\|/g, "\\|"),
        );
        lines.push("| " + cells.join(" | ") + " |");
        if (i === 0) lines.push("| " + cells.map(() => "---").join(" | ") + " |");
      }
      return lines.join("\n");
    }
    case "figure":
      return `![Figure on page ${block.page}](figure-p${block.page})`;
    case "chart":
      return `*[${block.metadata?.chart_type ?? "chart"}: ${content}]*`;
    case "equation":
      return content.includes("\\") || content.includes("{")
        ? `$$\n${content}\n$$`
        : `$$${content}$$`;
    case "header":
    case "footer":
      return `*${content}*`;
    case "unknown":
      return `> ${content}`;
    default:
      return content;
  }
}
