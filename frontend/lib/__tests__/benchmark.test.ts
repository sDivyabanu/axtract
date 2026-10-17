import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ALL_SELECTION,
  buildSeries,
  estimatePdfs,
  fmtDelta,
  fmtDuration,
  friendlyError,
  headline,
  isActive,
  pickRunToRestore,
  progressPercent,
  scoreBadge,
  selectOnly,
  selectedNames,
  toggleCategory,
} from "../benchmark-utils";

const CATS = ["arxiv_math", "headers_footers", "tables"];

test("choosing a category narrows from all; choosing none or every category goes back to all", () => {
  let sel = toggleCategory(ALL_SELECTION, "tables", CATS);
  assert.deepEqual(sel, { all: false, chosen: ["tables"] });
  sel = toggleCategory(sel, "arxiv_math", CATS);
  assert.deepEqual(sel.chosen, ["arxiv_math", "tables"]); // canonical order, not click order
  sel = toggleCategory(sel, "headers_footers", CATS);
  assert.equal(sel.all, true); // all three picked individually == all categories
  sel = toggleCategory(toggleCategory(ALL_SELECTION, "tables", CATS), "tables", CATS);
  assert.equal(sel.all, true); // un-picking the last one never leaves an empty selection
});

test("clicking a category while 'all' is shown starts a fresh selection instead of toggling it off", () => {
  assert.deepEqual(toggleCategory(ALL_SELECTION, "arxiv_math", CATS), { all: false, chosen: ["arxiv_math"] });
});

test("selectOnly applies a recommendation and ignores unknown names", () => {
  assert.deepEqual(selectOnly(["tables", "nope"], CATS), { all: false, chosen: ["tables"] });
  assert.equal(selectOnly([], CATS).all, true);
  assert.equal(selectOnly(CATS, CATS).all, true);
  assert.deepEqual(selectedNames(ALL_SELECTION, CATS), CATS);
});

test("the PDF estimate never exceeds what a category has", () => {
  const cats = [{ name: "a", pdfs: 3 }, { name: "b", pdfs: 100 }];
  assert.equal(estimatePdfs(ALL_SELECTION, cats, 5), 8);
  assert.equal(estimatePdfs({ all: false, chosen: ["b"] }, cats, 5), 5);
});

test("quick and incomplete results are never labelled as official", () => {
  assert.match(scoreBadge("quick_sample").detail, /Not a full official score/);
  assert.equal(scoreBadge("quick_sample").tone, "amber");
  assert.equal(scoreBadge("full_incomplete").tone, "red");
  assert.match(scoreBadge("imported_invalid").detail, /diagnostic only/);
  assert.equal(scoreBadge("full_official").tone, "green");
});

test("a diagnostic average is never shown as a plain score", () => {
  assert.deepEqual(headline(71.34, 10), { value: "71.3%", caption: "score" });
  assert.deepEqual(headline(null, 22.28), { value: "22.3%", caption: "diagnostic only, not a valid score" });
  assert.equal(headline(null, null).value, "n/a");
});

test("progress counts failures as processed and clamps to 100%", () => {
  assert.equal(progressPercent({ total: 10, done: 6, failed: 2 }), 80);
  assert.equal(progressPercent({ total: 10, done: 10, failed: 5 }), 100);
  assert.equal(progressPercent({ total: 0, done: 0, failed: 0 }), 0);
  assert.equal(progressPercent(null), 0);
});

test("documents with missing pages count as processed but are shown separately", () => {
  assert.equal(progressPercent({ total: 10, done: 5, failed: 1, partial: 2 }), 80);
  assert.equal(progressPercent({ total: 4, done: 1, failed: 0 }), 25); // partial is optional (imported runs)
});

test("refresh recovery prefers the active run, then the newest", () => {
  assert.equal(pickRunToRestore("qk-active", [{ run_id: "qk-old" }]), "qk-active");
  assert.equal(pickRunToRestore(null, [{ run_id: "qk-new" }, { run_id: "qk-old" }]), "qk-new");
  assert.equal(pickRunToRestore(null, []), null);
  assert.equal(isActive("running"), true);
  assert.equal(isActive("cancelled"), false);
});

test("trend series leave gaps for missing values and centre a single point", () => {
  const segs = buildSeries([10, null, 50, 90], ["a", "b", "c", "d"], 100, 50, 0);
  assert.equal(segs.length, 2);
  assert.equal(segs[1].length, 2);
  assert.ok(Math.abs(segs[1][1].y - 5) < 1e-9); // 90% is near the top
  const single = buildSeries([50], ["a"], 100, 50, 0);
  assert.equal(single[0][0].x, 50);
  assert.deepEqual(buildSeries([null], ["a"], 100, 50), []);
});

test("formatting helpers", () => {
  assert.equal(fmtDuration(59), "59s");
  assert.equal(fmtDuration(125), "2m 5s");
  assert.equal(fmtDuration(3700), "1h 1m");
  assert.equal(fmtDelta(2.5), "+2.5");
  assert.equal(fmtDelta(-1), "-1");
  assert.equal(fmtDelta(null), "n/a");
});

test("known backend errors become actionable messages", () => {
  assert.match(friendlyError("OFFICIAL_BENCHMARK_ACTIVE", "x"), /official benchmark is running/);
  assert.equal(friendlyError("SOMETHING_ELSE", "raw message"), "raw message");
  assert.equal(friendlyError(undefined, "fallback"), "fallback");
});
