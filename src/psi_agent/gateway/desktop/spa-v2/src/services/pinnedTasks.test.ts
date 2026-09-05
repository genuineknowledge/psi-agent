import { describe, expect, it } from "vitest";
import {
  normalizePinnedIds,
  prunePinnedTaskIds,
  sortTasksByPin,
  togglePinnedTaskId,
} from "./pinnedTasks";

describe("normalizePinnedIds", () => {
  it("dedupes and trims", () => {
    expect(normalizePinnedIds([" a ", "b", "a", "", 1, null])).toEqual(["a", "b"]);
  });
});

describe("togglePinnedTaskId", () => {
  it("adds then removes", () => {
    expect(togglePinnedTaskId([], "t1")).toEqual(["t1"]);
    expect(togglePinnedTaskId(["t1", "t2"], "t1")).toEqual(["t2"]);
  });
});

describe("prunePinnedTaskIds", () => {
  it("keeps only active ids", () => {
    expect(prunePinnedTaskIds(["a", "b", "c"], ["b", "c", "d"])).toEqual(["b", "c"]);
  });
});

describe("sortTasksByPin", () => {
  it("puts pinned ahead and keeps relative order within groups", () => {
    const tasks = [{ id: "1" }, { id: "2" }, { id: "3" }, { id: "4" }];
    expect(sortTasksByPin(tasks, ["3", "1"]).map((t) => t.id)).toEqual([
      "3",
      "1",
      "2",
      "4",
    ]);
  });

  it("leaves list unchanged when nothing is pinned", () => {
    const tasks = [{ id: "1" }, { id: "2" }];
    expect(sortTasksByPin(tasks, [])).toBe(tasks);
  });
});
