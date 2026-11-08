import { describe, expect, it } from "vitest";
import { MAX_RECENT, withRecentStop, type RecentStop } from "./recentStops";

const stop = (id: string, name = `Stop ${id}`): RecentStop => ({ id, name, coordinate: [21, 52.2] });

describe("recent stops", () => {
  it("puts the newest stop first", () => {
    expect(withRecentStop([stop("a"), stop("b")], stop("c")).map((item) => item.id)).toEqual(["c", "a", "b"]);
  });
  it("moves a station opened again to the top instead of listing it twice", () => {
    expect(withRecentStop([stop("a"), stop("b")], stop("b")).map((item) => item.id)).toEqual(["b", "a"]);
  });
  it("treats another platform of the same station as the same entry", () => {
    const list = withRecentStop([stop("1", "Centrum"), stop("2", "Wilanowska")], stop("3", "Centrum"));
    expect(list.map((item) => item.id)).toEqual(["3", "2"]);
  });
  it("keeps at most a few stops", () => {
    let list: RecentStop[] = [];
    for (let i = 0; i < MAX_RECENT + 3; i++) list = withRecentStop(list, stop(String(i)));
    expect(list).toHaveLength(MAX_RECENT);
    expect(list[0].id).toBe(String(MAX_RECENT + 2));
  });
});
