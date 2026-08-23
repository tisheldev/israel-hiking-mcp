/**
 * The boundary: whatever the host handed over, turned into a `MapViewModel`.
 *
 * Everything here is a pure function of its input, which is what makes the
 * interesting cases — a route recorded as four disconnected pieces, a router
 * that snapped both ends 300 m — testable without a host or a browser.
 *
 * Two rules the rest of the component depends on:
 *
 * **Nothing is recomputed.** Lengths, ascent, warnings and unknowns are copied
 * from the result and formatted. This server is careful about what its numbers
 * mean, and a figure derived in the browser from a *simplified* line would
 * quietly contradict the one the tool returned.
 *
 * **A shape that does not validate is not drawn at all.** Dropping a bad
 * position and drawing the rest would redraw the route as a shortcut it never
 * took, on a map somebody might walk from. An honest "cannot draw this" leaves
 * the tool's own text result standing.
 */

import type {
  AttributionView,
  LatLng,
  LinePart,
  MapGap,
  MapMarker,
  MapViewModel,
  Metric,
  ViewState,
} from "./types.js";

/** GeoJSON order: longitude first, then latitude. */
type Position = readonly [number, number, ...number[]];

const MIN_POSITIONS_PER_PART = 2;

// --- reading unknown values safely -------------------------------------------

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringAt(source: Record<string, unknown>, key: string): string | null {
  const value = source[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function numberAt(source: Record<string, unknown>, key: string): number | null {
  const value = source[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function objectAt(
  source: Record<string, unknown>,
  key: string,
): Record<string, unknown> | null {
  const value = source[key];
  return isObject(value) ? value : null;
}

/**
 * Upstream string lists, copied verbatim.
 *
 * A malformed list yields an empty one rather than a failure: warnings are
 * important enough to render whenever they are there, and not so structural
 * that their absence should hide the map.
 */
function stringsAt(source: Record<string, unknown>, key: string): string[] {
  const value = source[key];
  if (!Array.isArray(value)) return [];
  return value.filter((entry): entry is string => typeof entry === "string");
}

// --- geometry ----------------------------------------------------------------

function isValidPosition(value: unknown): value is Position {
  if (!Array.isArray(value) || value.length < 2) return false;
  const [lng, lat] = value;
  return (
    typeof lng === "number" &&
    typeof lat === "number" &&
    Number.isFinite(lng) &&
    Number.isFinite(lat) &&
    lng >= -180 &&
    lng <= 180 &&
    lat >= -90 &&
    lat <= 90
  );
}

function toLatLng(position: Position): LatLng {
  return { lat: position[1], lng: position[0] };
}

/**
 * One GeoJSON line's positions, or `null` if any of them is unusable.
 *
 * All-or-nothing per part, deliberately. See the module note: a line with a
 * position quietly removed is a different route.
 */
function toLinePart(value: unknown): LinePart | null {
  if (!Array.isArray(value) || value.length < MIN_POSITIONS_PER_PART) return null;
  const part: LatLng[] = [];
  for (const position of value) {
    if (!isValidPosition(position)) return null;
    part.push(toLatLng(position));
  }
  return part;
}

/**
 * A `LineString` or `MultiLineString` as independent parts.
 *
 * Each `MultiLineString` member stays its own part. The gaps between them are
 * in the map data — the route is mapped with pieces missing, or was saved as
 * several trips — and closing them here would invent a connection.
 */
export function linePartsOf(geometry: unknown): readonly LinePart[] | null {
  if (!isObject(geometry)) return null;
  const coordinates = geometry["coordinates"];

  if (geometry["type"] === "LineString") {
    const part = toLinePart(coordinates);
    return part === null ? null : [part];
  }

  if (geometry["type"] === "MultiLineString") {
    if (!Array.isArray(coordinates) || coordinates.length === 0) return null;
    const parts: LinePart[] = [];
    for (const member of coordinates) {
      const part = toLinePart(member);
      if (part === null) return null;
      parts.push(part);
    }
    return parts;
  }

  return null;
}

function coordinatesAt(
  source: Record<string, unknown>,
  key: string,
): LatLng | null {
  const point = objectAt(source, key);
  if (point === null) return null;
  const lat = numberAt(point, "lat");
  const lng = numberAt(point, "lng");
  if (lat === null || lng === null) return null;
  if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null;
  return { lat, lng };
}

// --- display formatting ------------------------------------------------------

/**
 * Numbers as the tool already rounded them.
 *
 * `toLocaleString` with no locale follows the host's, which is what a reader
 * expects; the value itself is untouched.
 */
function km(value: number): string {
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })} km`;
}

function meters(value: number): string {
  return `${Math.round(value).toLocaleString()} m`;
}

function metric(label: string, value: string | null): Metric | null {
  return value === null ? null : { label, value };
}

function metricsOf(entries: readonly (Metric | null)[]): readonly Metric[] {
  return entries.filter((entry): entry is Metric => entry !== null);
}

/**
 * What was done to the returned shape.
 *
 * Only ever a restatement of `geometryDetail`: whether the tool thinned the
 * line, and how many separate pieces the route is drawn in. Both change what
 * the picture means, and neither is visible in the picture itself.
 */
function geometryNotes(
  detail: Record<string, unknown> | null,
  partCount: number,
): readonly string[] {
  const notes: string[] = [];

  if (detail !== null && detail["simplified"] === true) {
    const shown = numberAt(detail, "pointCount");
    const recorded = numberAt(detail, "recordedPointCount");
    const tolerance = numberAt(detail, "toleranceMeters");
    if (shown !== null && recorded !== null) {
      const within =
        tolerance === null ? "" : `, dropping detail finer than ${tolerance} m`;
      notes.push(
        `The drawn line was thinned from ${recorded.toLocaleString()} positions to ` +
          `${shown.toLocaleString()}${within}. It still starts and ends where the ` +
          `original does.`,
      );
    }
  }

  if (partCount > 1) {
    notes.push(
      `Drawn as ${partCount} separate lines that do not meet end to end. The gaps ` +
        `are in the data — they are not drawn, and nothing says the ground between ` +
        `them can be crossed.`,
    );
  }

  return notes;
}

function attributionOf(source: Record<string, unknown>): AttributionView {
  const attribution = objectAt(source, "attribution");
  if (attribution === null) return { notice: "", sources: [] };
  return {
    notice: stringAt(attribution, "notice") ?? "",
    sources: stringsAt(attribution, "sources"),
  };
}

// --- telling the two results apart -------------------------------------------

/**
 * A `get_route_details` result: something a person recorded.
 *
 * Identified by the fields only that tool returns — a `ref` naming the source
 * that owns the route, and the recorded start point. Checked once, here, so the
 * renderer never has to ask what shape it was given.
 */
export function isRecordedRoute(value: unknown): value is Record<string, unknown> {
  return (
    isObject(value) &&
    isObject(value["ref"]) &&
    isObject(value["startPoint"]) &&
    isObject(value["geometry"])
  );
}

/**
 * A `route_between_points` result: something a router computed just now.
 *
 * Identified by its two `PathEnd`s, which no recorded route carries. They are
 * also the reason this shape needs its own adapter at all: an end that was
 * snapped to the nearest mapped way has to be drawn as two points, not one.
 */
export function isCalculatedRoute(value: unknown): value is Record<string, unknown> {
  if (!isObject(value) || !isObject(value["geometry"])) return false;
  const start = value["start"];
  const end = value["end"];
  return (
    isObject(start) &&
    isObject(end) &&
    isObject(start["requested"]) &&
    isObject(start["onPath"]) &&
    isObject(end["requested"]) &&
    isObject(end["onPath"])
  );
}

// --- adapters ----------------------------------------------------------------

/**
 * Markers for the ends of a recorded line.
 *
 * Taken from the drawn geometry rather than from `startPoint`, so the marker
 * always sits on the line the viewer can see. The two can differ once a long
 * track has been thinned.
 */
function recordedEndMarkers(parts: readonly LinePart[]): readonly MapMarker[] {
  const firstPart = parts[0];
  const lastPart = parts[parts.length - 1];
  if (firstPart === undefined || lastPart === undefined) return [];

  const first = firstPart[0];
  const last = lastPart[lastPart.length - 1];
  if (first === undefined || last === undefined) return [];

  return [
    { position: first, role: "start", label: "First recorded position" },
    { position: last, role: "end", label: "Last recorded position" },
  ];
}

function recordedRouteView(result: Record<string, unknown>): MapViewModel | null {
  const lineParts = linePartsOf(result["geometry"]);
  if (lineParts === null || lineParts.length === 0) return null;

  const lengthKm = numberAt(result, "lengthKm");
  const ascent = numberAt(result, "ascentMeters");
  const descent = numberAt(result, "descentMeters");

  return {
    kind: "recorded",
    title: stringAt(result, "title") ?? "Recorded route",
    metrics: metricsOf([
      metric("Length", lengthKm === null ? null : km(lengthKm)),
      metric("Difficulty", stringAt(result, "difficulty")),
      metric("Ascent", ascent === null ? null : meters(ascent)),
      metric("Descent", descent === null ? null : meters(descent)),
      metric("Activity", stringAt(result, "activity")),
    ]),
    lineParts,
    markers: recordedEndMarkers(lineParts),
    gaps: [],
    notes: geometryNotes(objectAt(result, "geometryDetail"), lineParts.length),
    warnings: stringsAt(result, "warnings"),
    unknowns: stringsAt(result, "unknowns"),
    attribution: attributionOf(result),
  };
}

/**
 * One end of a calculated path, as up to two markers and the gap between them.
 *
 * When the router had to move an end, both points are shown and the ground
 * between them is drawn as its own dashed segment. That ground is a walk the
 * path does not include, and nothing establishes it can be crossed at all —
 * so it must not look like part of the route.
 */
function calculatedEnd(
  end: Record<string, unknown>,
  which: "Start" | "End",
): { markers: MapMarker[]; gaps: MapGap[] } {
  const requested = coordinatesAt(end, "requested");
  const onPath = coordinatesAt(end, "onPath");
  if (requested === null || onPath === null) return { markers: [], gaps: [] };

  const apart = numberAt(end, "metersApart") ?? 0;
  const markers: MapMarker[] = [
    {
      position: onPath,
      role: which === "Start" ? "start" : "end",
      label: `${which} of the calculated path`,
    },
  ];
  const gaps: MapGap[] = [];

  // Identical points would stack two markers and draw a zero-length gap.
  if (requested.lat !== onPath.lat || requested.lng !== onPath.lng) {
    markers.push({
      position: requested,
      role: "requested",
      label: `${which} point requested — ${meters(apart)} from the path`,
    });
    gaps.push({
      from: requested,
      to: onPath,
      label: `${meters(apart)} between the requested ${which.toLowerCase()} and the path. Not part of the route.`,
    });
  }

  return { markers, gaps };
}

function calculatedRouteView(result: Record<string, unknown>): MapViewModel | null {
  const lineParts = linePartsOf(result["geometry"]);
  if (lineParts === null || lineParts.length === 0) return null;

  const start = objectAt(result, "start");
  const end = objectAt(result, "end");
  const atStart = start === null ? { markers: [], gaps: [] } : calculatedEnd(start, "Start");
  const atEnd = end === null ? { markers: [], gaps: [] } : calculatedEnd(end, "End");

  const activity = stringAt(result, "activity");
  const lengthKm = numberAt(result, "lengthKm");
  const straightLineKm = numberAt(result, "straightLineKm");

  return {
    kind: "calculated",
    title: activity === null ? "Calculated path" : `Calculated ${activity} path`,
    metrics: metricsOf([
      metric("Length", lengthKm === null ? null : km(lengthKm)),
      metric("Straight line", straightLineKm === null ? null : km(straightLineKm)),
    ]),
    lineParts,
    markers: [...atStart.markers, ...atEnd.markers],
    gaps: [...atStart.gaps, ...atEnd.gaps],
    notes: geometryNotes(objectAt(result, "geometryDetail"), lineParts.length),
    warnings: stringsAt(result, "warnings"),
    unknowns: stringsAt(result, "unknowns"),
    attribution: attributionOf(result),
  };
}

// --- the boundary ------------------------------------------------------------

const NOT_A_ROUTE =
  "This result has no route line to draw. The tool's own answer above is complete.";

const BROKEN_GEOMETRY =
  "This route's shape could not be read as a valid line, so it is not drawn " +
  "rather than drawn wrongly. The tool's own answer above is complete.";

/**
 * The payload a tool actually returned.
 *
 * Hosts deliver the whole `CallToolResult`, but a test — and a host that
 * unwraps for us — may pass the structured payload directly. Accepting both
 * keeps the rest of the module free of envelope handling.
 */
function payloadOf(result: unknown): unknown {
  if (isObject(result) && isObject(result["structuredContent"])) {
    return result["structuredContent"];
  }
  return result;
}

/**
 * A host's tool result as something the renderer can show.
 *
 * Never throws. The three states it can return are the three things that can
 * be true: a route to draw, a result that has no route in it, and a route
 * whose shape did not survive validation.
 */
export function toViewState(result: unknown): ViewState {
  const payload = payloadOf(result);

  if (isCalculatedRoute(payload)) {
    const model = calculatedRouteView(payload);
    return model === null
      ? { status: "unrenderable", reason: BROKEN_GEOMETRY }
      : { status: "ready", model };
  }

  if (isRecordedRoute(payload)) {
    const model = recordedRouteView(payload);
    return model === null
      ? { status: "unrenderable", reason: BROKEN_GEOMETRY }
      : { status: "ready", model };
  }

  return { status: "unrenderable", reason: NOT_A_ROUTE };
}
