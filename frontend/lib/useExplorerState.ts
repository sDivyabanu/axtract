"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { BlockType, DocumentResponse } from "./types";
import {
  type ConfidenceFilter,
  type FilterState,
  type ReviewFilter,
  createInitialFilters,
  filterBlocks,
  findMatchIndices,
  getUniqueExtractors,
  hasActiveFilters,
  parsePageInput,
} from "./filters";

const DEBOUNCE_MS = 200;

export function useExplorerState(result: DocumentResponse) {
  const [filters, setFilters] = useState<FilterState>(() => createInitialFilters());
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [currentMatchIndex, setCurrentMatchIndex] = useState(0);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const extractors = useMemo(() => getUniqueExtractors(result.blocks), [result.blocks]);

  const setQuery = useCallback(
    (q: string) => {
      setFilters((f) => ({ ...f, query: q }));
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        setDebouncedQuery(q);
        setCurrentMatchIndex(0);
      }, DEBOUNCE_MS);
    },
    [],
  );

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  const setPageInput = useCallback(
    (input: string) => {
      const pages = parsePageInput(input, result.page_count);
      setFilters((f) => ({ ...f, pageInput: input, pages }));
    },
    [result.page_count],
  );

  const goToPage = useCallback(
    (page: number) => {
      if (page >= 1 && page <= result.page_count) {
        const input = String(page);
        setFilters((f) => ({
          ...f,
          pageInput: input,
          pages: [page],
        }));
      }
    },
    [result.page_count],
  );

  const toggleType = useCallback((type: BlockType) => {
    setFilters((f) => {
      const next = new Set(f.types);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return { ...f, types: next };
    });
  }, []);

  const toggleExtractor = useCallback((ext: string) => {
    setFilters((f) => {
      const next = new Set(f.extractors);
      if (next.has(ext)) next.delete(ext);
      else next.add(ext);
      return { ...f, extractors: next };
    });
  }, []);

  const setConfidence = useCallback((c: ConfidenceFilter) => {
    setFilters((f) => ({ ...f, confidence: c }));
  }, []);

  const setReview = useCallback((r: ReviewFilter) => {
    setFilters((f) => ({ ...f, review: r }));
  }, []);

  const clearFilters = useCallback(() => {
    setFilters(createInitialFilters());
    setDebouncedQuery("");
    setCurrentMatchIndex(0);
  }, []);

  const removeFilter = useCallback(
    (key: string) => {
      setFilters((f) => {
        const next = { ...f };
        switch (key) {
          case "query":
            next.query = "";
            setDebouncedQuery("");
            break;
          case "pages":
            next.pageInput = "";
            next.pages = null;
            break;
          case "types":
            next.types = new Set();
            break;
          case "extractors":
            next.extractors = new Set();
            break;
          case "confidence":
            next.confidence = "all";
            break;
          case "review":
            next.review = "all";
            break;
        }
        return next;
      });
    },
    [],
  );

  const effectiveFilters = useMemo<FilterState>(
    () => ({ ...filters, query: debouncedQuery }),
    [filters, debouncedQuery],
  );

  const filtered = useMemo(
    () => filterBlocks(result.blocks, effectiveFilters),
    [result.blocks, effectiveFilters],
  );

  const matchInfo = useMemo(
    () => findMatchIndices(filtered, debouncedQuery),
    [filtered, debouncedQuery],
  );

  const totalMatches = useMemo(
    () => matchInfo.reduce((sum, m) => sum + m.positions.length, 0),
    [matchInfo],
  );

  const matchPages = useMemo(() => {
    const pages = new Set<number>();
    for (const m of matchInfo) {
      pages.add(filtered[m.blockIndex].page);
    }
    return pages.size;
  }, [matchInfo, filtered]);

  const nextMatch = useCallback(() => {
    setCurrentMatchIndex((i) => (totalMatches > 0 ? (i + 1) % totalMatches : 0));
  }, [totalMatches]);

  const prevMatch = useCallback(() => {
    setCurrentMatchIndex((i) =>
      totalMatches > 0 ? (i - 1 + totalMatches) % totalMatches : 0,
    );
  }, [totalMatches]);

  const active = hasActiveFilters(effectiveFilters);

  return {
    filters,
    effectiveFilters,
    filtered,
    extractors,
    active,
    matchInfo,
    totalMatches,
    matchPages,
    currentMatchIndex,
    setQuery,
    setPageInput,
    goToPage,
    toggleType,
    toggleExtractor,
    setConfidence,
    setReview,
    clearFilters,
    removeFilter,
    nextMatch,
    prevMatch,
    debouncedQuery,
  };
}
