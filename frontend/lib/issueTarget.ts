import type { BlockPreview, DocumentBlock, ValidationIssue } from "./types";

/** Where an issue should be shown in the existing source viewer, and which block (if any) to select. */
export interface IssueTarget {
  block: DocumentBlock | null;
  /** Page + box for the viewer; null when the issue has no locatable source position. */
  target: BlockPreview | null;
}

/**
 * Resolve an issue's location, most precise first:
 *   1. the viewer position AXTRACT already computed for it (Office formats: locator.preview)
 *   2. its own page + bbox (PDF, PPTX, images)
 *   3. the related block's own position (same rule the block list uses)
 *   4. its page alone
 * Nothing is invented: an issue with none of these has no target.
 */
export function resolveIssueTarget(
  issue: ValidationIssue,
  blocks: DocumentBlock[],
  blockTarget: (b: DocumentBlock) => BlockPreview | null,
): IssueTarget {
  const loc = issue.locator;
  const block = loc.block_ids.map((id) => blocks.find((b) => b.id === id)).find((b): b is DocumentBlock => b !== undefined) ?? null;
  let target: BlockPreview | null = null;
  if (loc.preview && typeof loc.preview.page === "number") {
    target = { page: loc.preview.page, bbox: loc.preview.bbox ?? null };
  } else if (loc.page && loc.bbox) {
    target = { page: loc.page, bbox: loc.bbox };
  } else if (block) {
    target = blockTarget(block);
  } else if (loc.page) {
    target = { page: loc.page, bbox: null };
  }
  return { block, target };
}
