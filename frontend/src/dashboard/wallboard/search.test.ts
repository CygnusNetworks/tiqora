import { describe, expect, it } from "vitest";
import { parseWallboardIds, validateWallboardSearch } from "./search";

describe("parseWallboardIds", () => {
  it("accepts a number, a string and a comma list of positive integers", () => {
    expect(parseWallboardIds(7)).toEqual([7]);
    expect(parseWallboardIds("7")).toEqual([7]);
    expect(parseWallboardIds("7, 9,11")).toEqual([7, 9, 11]);
  });
  it("drops junk and duplicates and keeps at most ten", () => {
    expect(parseWallboardIds("7,x,0,-3,7,2.5,9")).toEqual([7, 9]);
    expect(parseWallboardIds("1,2,3,4,5,6,7,8,9,10,11,12")).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect(parseWallboardIds(undefined)).toEqual([]);
    expect(parseWallboardIds({})).toEqual([]);
  });
});

describe("validateWallboardSearch", () => {
  it("normalises ids to a comma string and keeps rotate within 15..900", () => {
    expect(validateWallboardSearch({ ids: 7, rotate: 30 })).toEqual({ ids: "7", rotate: 30 });
    expect(validateWallboardSearch({ ids: "7,9", rotate: "45" })).toEqual({ ids: "7,9", rotate: 45 });
    expect(validateWallboardSearch({ rotate: 14 })).toEqual({});
    expect(validateWallboardSearch({ rotate: 901 })).toEqual({});
    expect(validateWallboardSearch({ rotate: 15.5, ids: "x" })).toEqual({});
    expect(validateWallboardSearch({ rotate: 900 })).toEqual({ rotate: 900 });
  });
});
