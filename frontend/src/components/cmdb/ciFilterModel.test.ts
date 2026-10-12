import { describe, expect, it } from "vitest";
import type { CiSearchCriteria } from "@/lib/cmdbApi";
import { emptyFilterState, fromSavedCriteria, toCriteria, toSearchBody, activeFilterCount, validateCmdbListSearch } from "./ciFilterModel";

const SORT = { sort: "number", order: "asc" } as const;

describe("ciFilterModel", () => {
  it("builds an empty body without filter", () => {
    const body = toSearchBody(emptyFilterState(), 1, SORT);
    expect(body).toEqual({ class_ids: [], q: null, filter: null, sort: "number", order: "asc", offset: 0, limit: 50 });
  });

  it("round-trips a saved criteria into the same search part", () => {
    const criteria: CiSearchCriteria = {
      class_ids: [2],
      q: "web",
      sort: "changed_at",
      order: "desc",
      filter: {
        version: 1,
        where: {
          all: [
            { field: "deployment_state", op: "in", value: ["1", "2"] },
            { field: "incident_functionality", op: "in", value: ["warning", "incident"] },
            { field: "attr.ip", op: "contains", value: "192.0.2" },
            { field: "attr.cpu", op: "gte", value: 4 },
            { field: "attr.serial", op: "is_empty" },
          ],
        },
      },
    };
    const state = fromSavedCriteria(criteria);
    expect(state).toEqual({
      classId: 2,
      q: "web",
      deployment: [1, 2],
      incident: ["warning", "incident"],
      attr: [
        { field: "attr.ip", op: "contains", value: "192.0.2" },
        { field: "attr.cpu", op: "gte", value: 4 },
        { field: "attr.serial", op: "is_empty", value: undefined },
      ],
    });
    const body = toSearchBody(state, 1, { sort: "changed_at", order: "desc" });
    const { sort, order, class_ids, q, filter } = criteria;
    expect({ sort: body.sort, order: body.order, class_ids: body.class_ids, q: body.q, filter: body.filter }).toEqual({
      sort,
      order,
      class_ids,
      q,
      filter,
    });
    expect(toCriteria(state, { sort: "changed_at", order: "desc" })).toEqual({ ...criteria, filter });
  });

  it("drops attribute rows without a class and pages with offset", () => {
    const state = { ...emptyFilterState(), attr: [{ field: "attr.ip", op: "eq", value: "x" }] };
    expect(toSearchBody(state, 3, SORT).filter).toBeNull();
    expect(toSearchBody(state, 3, SORT).offset).toBe(100);
  });

  it("skips attribute rows with no field or no value, keeps value-less ops", () => {
    const state = {
      ...emptyFilterState(),
      classId: 1,
      attr: [
        { field: "", op: "eq", value: "x" },
        { field: "attr.ip", op: "eq", value: "" },
        { field: "attr.ip", op: "is_not_empty", value: undefined },
      ],
    };
    expect(toSearchBody(state, 1, SORT).filter?.where).toEqual({ all: [{ field: "attr.ip", op: "is_not_empty" }] });
  });

  it("tolerates unknown criteria shapes", () => {
    const state = fromSavedCriteria({ sort: "number", order: "asc", filter: { version: 1, where: { any: [] } } });
    expect(state).toEqual(emptyFilterState());
    expect(activeFilterCount({ ...emptyFilterState(), q: "a", deployment: [1] })).toBe(2);
  });
});

describe("validateCmdbListSearch", () => {
  it("parses numbers defensively and drops junk", () => {
    expect(validateCmdbListSearch({ class: "2", page: "3", saved: 7, sort: "name", order: "desc", q: "web" })).toEqual({
      class: 2,
      page: 3,
      saved: 7,
      sort: "name",
      order: "desc",
      q: "web",
    });
    expect(validateCmdbListSearch({ class: "abc", page: -1, saved: "1.5", sort: "bogus", order: "up", q: "" })).toEqual({});
    // all-digit search texts arrive as numbers from the router's codec
    expect(validateCmdbListSearch({ q: 4711 })).toEqual({ q: "4711" });
    expect(validateCmdbListSearch({ page: 1 })).toEqual({});
  });
});

