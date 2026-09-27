/**
 * The vocabulary the renderer speaks, and the two interfaces the composition
 * root wires together.
 *
 * Nothing here mentions MCP or Leaflet. That is the point: `normalize.ts`
 * produces a `MapViewModel` from whatever a host delivered, `map.ts` draws one,
 * and neither has to know the other exists. A future POI overlay adds fields
 * here and a layer in `map.ts` without touching the bridge or the server.
 *
 * Every string in a view model is untrusted upstream text. It is placed with
 * `textContent`, never `innerHTML`.
 */

/** A point in the order people say it: latitude, then longitude. */
export interface LatLng {
  readonly lat: number;
  readonly lng: number;
}

/**
 * One independent drawn line.
 *
 * Parts are never concatenated. A route recorded as several lines has gaps that
 * are in the map data, and joining them would draw a path that nobody recorded.
 */
export type LinePart = readonly LatLng[];

/** Whether the geometry was recorded by a person or computed by a router. */
export type RouteKind = "recorded" | "calculated";

/**
 * What a marker means, which is also how it is drawn.
 *
 * `start` and `end` are always where the drawn line actually begins and ends.
 * `requested` appears only for calculated routes, marking a point the router
 * was asked for but could not reach: drawing only the reached end would hide a
 * walk the path does not cover.
 */
export type MarkerRole = "start" | "end" | "requested";

export interface MapMarker {
  readonly position: LatLng;
  readonly role: MarkerRole;
  /** Accessible, human-readable label. Upstream text may appear here. */
  readonly label: string;
}

/**
 * The ground between a requested endpoint and where the path actually reaches.
 *
 * Drawn as its own dashed segment so the route line never implies the router
 * covered it.
 */
export interface MapGap {
  readonly from: LatLng;
  readonly to: LatLng;
  readonly label: string;
}

/** One labelled figure in the header. Values are pre-formatted for display. */
export interface Metric {
  readonly label: string;
  readonly value: string;
}

export interface AttributionView {
  readonly notice: string;
  readonly sources: readonly string[];
}

/**
 * Everything the renderer needs, and nothing else.
 *
 * Invariants every adapter must satisfy, whatever result it came from:
 *
 * - `lineParts` is non-empty, and every part holds at least two valid
 *   positions — so bounds always exist and every part is drawable.
 * - Coordinates are finite and in range; validation happened at the boundary.
 * - `warnings`, `unknowns` and `attribution` are the backend's own strings,
 *   copied rather than recomputed.
 */
export interface MapViewModel {
  readonly kind: RouteKind;
  readonly title: string;
  readonly metrics: readonly Metric[];
  readonly lineParts: readonly LinePart[];
  readonly markers: readonly MapMarker[];
  readonly gaps: readonly MapGap[];
  /** What was done to the shape — simplification, separate parts. */
  readonly notes: readonly string[];
  readonly warnings: readonly string[];
  readonly unknowns: readonly string[];
  readonly attribution: AttributionView;
}

/**
 * What the view is showing.
 *
 * `unrenderable` is a state rather than an exception: the host has already
 * shown the tool's text and structured result, so a map that cannot be drawn
 * should say so quietly, not blank the component or throw into the console.
 */
export type ViewState =
  | { readonly status: "waiting" }
  | { readonly status: "ready"; readonly model: MapViewModel }
  | { readonly status: "unrenderable"; readonly reason: string };

/**
 * Where tool results come from.
 *
 * The renderer and the normalizer never see this; `main.ts` owns the one
 * implementation. Narrow on purpose — the app needs a stream of results and a
 * way to start, and nothing in this component calls back into the server.
 */
export interface ResultSource {
  /** Register the listener that receives each raw result the host delivers. */
  onResult(listener: (result: unknown) => void): void;
  /** Connect to the host. Resolves once the handshake is complete. */
  start(): Promise<void>;
}

/**
 * Anything that can show a `ViewState`.
 *
 * Implemented by Leaflet in `map.ts`; a test double implements it in three
 * lines, which is what keeps `main.ts` testable without a browser.
 */
export interface MapRenderer {
  render(state: ViewState): void;
}
