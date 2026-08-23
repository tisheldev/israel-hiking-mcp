/**
 * The boundary is where this component can do harm, so it is where the tests
 * are: a gap silently closed, a position quietly dropped, or a warning lost
 * would all render as a confident picture of something that is not true.
 *
 * Leaflet is not mocked and not imported. Everything under test is a pure
 * function, and `connectRenderer` is exercised against a fake source and a fake
 * renderer — three lines each, which is the payoff for the interfaces in
 * `types.ts`.
 */

import { describe, expect, it } from "vitest";

import { connectRenderer } from "./main.js";
import { isCalculatedRoute, isRecordedRoute, linePartsOf, toViewState } from "./normalize.js";
import type { MapRenderer, MapViewModel, ResultSource, ViewState } from "./types.js";

const ATTRIBUTION = {
  notice: "Data from Israel Hiking Map / Mapeak.",
  sources: ["Israel Hiking Map / Mapeak — CC BY-NC-SA 3.0", "OpenStreetMap — ODbL"],
};

function recordedRoute(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ref: { source: "OSM", identifier: "relation_282071" },
    title: "Haifa Trail",
    description: null,
    activity: "Hiking",
    difficulty: "Moderate",
    lengthKm: 12.34,
    ascentMeters: 430,
    descentMeters: 402,
    startPoint: { lat: 32.81, lng: 34.99 },
    geometry: {
      type: "LineString",
      coordinates: [
        [34.99, 32.81],
        [35.0, 32.82],
        [35.01, 32.83],
      ],
    },
    ihmUrl: "https://israelhiking.osm.org.il/poi/OSM/relation_282071",
    geometryDetail: {
      pointCount: 3,
      recordedPointCount: 3,
      simplified: false,
      toleranceMeters: null,
    },
    unknowns: ["Whether the route is open, permitted or passable today."],
    warnings: ["This route is mapped in OpenStreetMap."],
    attribution: ATTRIBUTION,
    ...overrides,
  };
}

function calculatedRoute(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    activity: "Hiking",
    start: {
      requested: { lat: 32.7, lng: 35.0 },
      onPath: { lat: 32.701, lng: 35.002 },
      metersApart: 210,
    },
    end: {
      requested: { lat: 32.75, lng: 35.05 },
      onPath: { lat: 32.75, lng: 35.05 },
      metersApart: 0,
    },
    lengthKm: 7.2,
    straightLineKm: 6.1,
    geometry: {
      type: "LineString",
      coordinates: [
        [35.002, 32.701],
        [35.03, 32.72],
        [35.05, 32.75],
      ],
    },
    geometryDetail: {
      pointCount: 3,
      recordedPointCount: 3,
      simplified: false,
      toleranceMeters: null,
    },
    unknowns: ["Whether the way is open, permitted or passable."],
    warnings: ["This is a path a routing engine calculated over mapped ways just now."],
    attribution: ATTRIBUTION,
    ...overrides,
  };
}

/** The view model a result is expected to produce, or a test failure. */
function modelOf(result: unknown): MapViewModel {
  const state = toViewState(result);
  if (state.status !== "ready") {
    throw new Error(`expected a drawable route, got ${state.status}`);
  }
  return state.model;
}

describe("recorded routes", () => {
  it("normalizes a LineString into one drawable part", () => {
    const model = modelOf(recordedRoute());

    expect(model.kind).toBe("recorded");
    expect(model.title).toBe("Haifa Trail");
    expect(model.lineParts).toHaveLength(1);
    // GeoJSON is [lng, lat]; the renderer is handed {lat, lng}.
    expect(model.lineParts[0]?.[0]).toEqual({ lat: 32.81, lng: 34.99 });
    expect(model.gaps).toHaveLength(0);
  });

  it("marks the first and last drawn positions", () => {
    const model = modelOf(recordedRoute());

    expect(model.markers).toHaveLength(2);
    expect(model.markers[0]).toMatchObject({ role: "start", position: { lat: 32.81 } });
    expect(model.markers[1]).toMatchObject({ role: "end", position: { lat: 32.83 } });
  });

  it("shows the metrics the source recorded", () => {
    const labels = modelOf(recordedRoute()).metrics.map((entry) => entry.label);

    expect(labels).toEqual(["Length", "Difficulty", "Ascent", "Descent", "Activity"]);
  });

  it("leaves out optional metadata the source does not carry", () => {
    const model = modelOf(
      recordedRoute({ lengthKm: null, difficulty: null, ascentMeters: null, descentMeters: null }),
    );

    expect(model.metrics.map((entry) => entry.label)).toEqual(["Activity"]);
    // A route with no metadata is still a route worth drawing.
    expect(model.lineParts).toHaveLength(1);
  });

  it("copies warnings and unknowns rather than recomputing them", () => {
    const model = modelOf(recordedRoute());

    expect(model.warnings).toEqual(["This route is mapped in OpenStreetMap."]);
    expect(model.unknowns).toEqual([
      "Whether the route is open, permitted or passable today.",
    ]);
    expect(model.attribution).toEqual(ATTRIBUTION);
  });
});

describe("disconnected geometry", () => {
  const twoParts = {
    type: "MultiLineString",
    coordinates: [
      [
        [34.99, 32.81],
        [35.0, 32.82],
      ],
      [
        [35.2, 32.9],
        [35.21, 32.91],
      ],
    ],
  };

  it("keeps every MultiLineString member as its own part", () => {
    const model = modelOf(recordedRoute({ geometry: twoParts }));

    expect(model.lineParts).toHaveLength(2);
    expect(model.lineParts[0]).toHaveLength(2);
    expect(model.lineParts[1]).toHaveLength(2);
  });

  it("never bridges the gap between two parts", () => {
    const model = modelOf(recordedRoute({ geometry: twoParts }));

    // The end of part one and the start of part two are far apart, and nothing
    // in the model joins them: no gap segment, and no shared position.
    expect(model.gaps).toHaveLength(0);
    expect(model.lineParts[0]?.at(-1)).toEqual({ lat: 32.82, lng: 35.0 });
    expect(model.lineParts[1]?.[0]).toEqual({ lat: 32.9, lng: 35.2 });
  });

  it("says in a note that the route is drawn in separate pieces", () => {
    const model = modelOf(recordedRoute({ geometry: twoParts }));

    expect(model.notes.join(" ")).toContain("2 separate lines");
  });

  it("marks the ends of the whole route, across parts", () => {
    const model = modelOf(recordedRoute({ geometry: twoParts }));

    expect(model.markers[0]?.position).toEqual({ lat: 32.81, lng: 34.99 });
    expect(model.markers[1]?.position).toEqual({ lat: 32.91, lng: 35.21 });
  });
});

describe("simplified geometry", () => {
  it("reports the thinning the backend applied", () => {
    const model = modelOf(
      recordedRoute({
        geometryDetail: {
          pointCount: 500,
          recordedPointCount: 24_000,
          simplified: true,
          toleranceMeters: 12,
        },
      }),
    );

    const note = model.notes.join(" ");
    expect(note).toContain("24,000");
    expect(note).toContain("500");
    expect(note).toContain("12 m");
  });

  it("says nothing about thinning when none happened", () => {
    expect(modelOf(recordedRoute()).notes).toEqual([]);
  });
});

describe("calculated routes", () => {
  it("is detected as calculated, not recorded", () => {
    const result = calculatedRoute();

    expect(isCalculatedRoute(result)).toBe(true);
    expect(modelOf(result).kind).toBe("calculated");
  });

  it("shows the requested point and the snapped point separately", () => {
    const model = modelOf(calculatedRoute());
    const roles = model.markers.map((marker) => marker.role);

    // Start was snapped 210 m, so both points appear; end was not moved at all.
    expect(roles).toEqual(["start", "requested", "end"]);
    expect(model.markers[0]?.position).toEqual({ lat: 32.701, lng: 35.002 });
    expect(model.markers[1]?.position).toEqual({ lat: 32.7, lng: 35.0 });
  });

  it("draws the requested-to-snapped gap as its own segment", () => {
    const model = modelOf(calculatedRoute());

    expect(model.gaps).toHaveLength(1);
    expect(model.gaps[0]?.from).toEqual({ lat: 32.7, lng: 35.0 });
    expect(model.gaps[0]?.to).toEqual({ lat: 32.701, lng: 35.002 });
    expect(model.gaps[0]?.label).toContain("Not part of the route");
  });

  it("adds no marker or gap for an end the router did not move", () => {
    const model = modelOf(
      calculatedRoute({
        start: {
          requested: { lat: 32.701, lng: 35.002 },
          onPath: { lat: 32.701, lng: 35.002 },
          metersApart: 0,
        },
      }),
    );

    expect(model.gaps).toHaveLength(0);
    expect(model.markers.map((marker) => marker.role)).toEqual(["start", "end"]);
  });

  it("keeps the unconditional calculated-path warning", () => {
    expect(modelOf(calculatedRoute()).warnings[0]).toContain("routing engine calculated");
  });
});

describe("invalid geometry", () => {
  const rejected: readonly [string, unknown][] = [
    ["an empty LineString", { type: "LineString", coordinates: [] }],
    ["a single-position line", { type: "LineString", coordinates: [[35, 32]] }],
    ["an empty MultiLineString", { type: "MultiLineString", coordinates: [] }],
    [
      "a non-finite coordinate",
      { type: "LineString", coordinates: [[35, 32], [Number.NaN, 32.1]] },
    ],
    [
      "an out-of-range latitude",
      {
        type: "LineString",
        coordinates: [
          [35, 32],
          [35.1, 91],
        ],
      },
    ],
    [
      "an out-of-range longitude",
      {
        type: "LineString",
        coordinates: [
          [35, 32],
          [181, 32.1],
        ],
      },
    ],
    [
      "a position that is not a pair",
      { type: "LineString", coordinates: [[35, 32], [35.1]] },
    ],
    ["an unsupported geometry type", { type: "Polygon", coordinates: [] }],
  ];

  it.each(rejected)("rejects %s", (_name, geometry) => {
    expect(linePartsOf(geometry)).toBeNull();
  });

  it("falls back rather than throwing when the shape is unusable", () => {
    const state = toViewState(recordedRoute({ geometry: { type: "LineString", coordinates: [] } }));

    expect(state.status).toBe("unrenderable");
  });

  it("rejects a whole part rather than drawing it with a position removed", () => {
    // Dropping the bad middle position would draw a straight line from the
    // first to the last — a shortcut the route never took.
    const geometry = {
      type: "LineString",
      coordinates: [
        [35, 32],
        [999, 32.1],
        [35.2, 32.2],
      ],
    };

    expect(linePartsOf(geometry)).toBeNull();
  });

  it("does not draw the good parts of a MultiLineString when one is bad", () => {
    const geometry = {
      type: "MultiLineString",
      coordinates: [
        [
          [35, 32],
          [35.1, 32.1],
        ],
        [[35.2, 32.2]],
      ],
    };

    expect(linePartsOf(geometry)).toBeNull();
  });
});

describe("results that are not routes", () => {
  it.each([
    ["null", null],
    ["a string", "no route here"],
    ["an unrelated object", { places: [], query: "Haifa" }],
    ["a POI result", { pois: [], attribution: ATTRIBUTION }],
  ])("falls back for %s", (_name, result) => {
    const state = toViewState(result);

    expect(state.status).toBe("unrenderable");
    if (state.status === "unrenderable") {
      expect(state.reason).toContain("complete");
    }
  });

  it("tells the two route shapes apart", () => {
    expect(isRecordedRoute(recordedRoute())).toBe(true);
    expect(isCalculatedRoute(recordedRoute())).toBe(false);
    expect(isRecordedRoute({ places: [] })).toBe(false);
  });
});

describe("host envelopes", () => {
  it("reads the route out of a full tool result", () => {
    const envelope = {
      content: [{ type: "text", text: "{…}" }],
      structuredContent: recordedRoute(),
      isError: false,
    };

    expect(modelOf(envelope).title).toBe("Haifa Trail");
  });

  it("accepts a bare structured payload too", () => {
    expect(modelOf(recordedRoute()).title).toBe("Haifa Trail");
  });
});

describe("Hebrew content", () => {
  it("preserves Hebrew titles, warnings and unknowns unchanged", () => {
    const title = "שביל חיפה";
    const warning = "המסלול מופה בOpenStreetMap ואינו מאומת.";
    const model = modelOf(
      recordedRoute({ title, warnings: [warning], difficulty: null }),
    );

    expect(model.title).toBe(title);
    expect(model.warnings).toEqual([warning]);
    // No transliteration, no direction marks added, nothing trimmed.
    expect(model.title).toHaveLength(title.length);
  });
});

describe("connectRenderer", () => {
  function fakes(): { source: ResultSource; renderer: MapRenderer; states: ViewState[] } {
    const listeners: ((result: unknown) => void)[] = [];
    const states: ViewState[] = [];
    return {
      source: {
        onResult: (listener) => void listeners.push(listener),
        start: async () => {
          for (const listener of listeners) listener(recordedRoute());
        },
      },
      renderer: { render: (state) => void states.push(state) },
      states,
    };
  }

  it("shows a waiting state before any result arrives", () => {
    const { source, renderer, states } = fakes();

    connectRenderer(source, renderer);

    expect(states).toEqual([{ status: "waiting" }]);
  });

  it("renders each result the host delivers", async () => {
    const { source, renderer, states } = fakes();

    connectRenderer(source, renderer);
    await source.start();

    expect(states).toHaveLength(2);
    expect(states[1]?.status).toBe("ready");
  });
});
