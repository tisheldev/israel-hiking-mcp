/**
 * The Leaflet renderer: draws a `MapViewModel`, and knows nothing else.
 *
 * It cannot see an MCP message or a backend result shape — by the time
 * anything reaches here it is lines, markers and display text. That is what
 * lets a POI overlay be added later as one more layer, and what keeps the
 * safety-critical decisions (what counts as a line, what a gap means) in
 * `normalize.ts` where they are unit-testable.
 *
 * Every upstream string is placed with `textContent`. There is no `innerHTML`
 * in this file, and there should never be one: these strings are somebody
 * else's data.
 */

import L from "leaflet";
import "leaflet/dist/leaflet.css";

import type {
  LatLng,
  MapMarker,
  MapViewModel,
  MarkerRole,
  MapRenderer,
  ViewState,
} from "./types.js";

/**
 * A low-volume raster basemap, appropriate for a personal, non-commercial
 * project. The origin is the only one in the resource's CSP, and it is the
 * only network request this document makes.
 *
 * A public deployment should choose a provider sized for its traffic and
 * update `TILE_ORIGIN` in `ui.py` to match.
 */
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTRIBUTION = "© OpenStreetMap contributors";
const MAX_ZOOM = 17;

/** Padding around the fitted route, so end markers are not against the edge. */
const FIT_PADDING: L.PointTuple = [24, 24];

/**
 * A recorded route and a calculated one must not be mistakable for each other:
 * one is a line somebody walked, the other is a line a router drew a moment
 * ago. Different colour and different weight, not a subtle shade apart.
 */
const LINE_STYLE: Record<MapViewModel["kind"], L.PolylineOptions> = {
  recorded: { color: "#c2410c", weight: 5, opacity: 0.9 },
  calculated: { color: "#1d4ed8", weight: 4, opacity: 0.9 },
};

/** Ground the route does not cover. Thin, dashed and grey — clearly not route. */
const GAP_STYLE: L.PolylineOptions = {
  color: "#525252",
  weight: 2,
  opacity: 0.9,
  dashArray: "6 6",
};

const MARKER_STYLE: Record<MarkerRole, L.CircleMarkerOptions> = {
  start: { radius: 7, color: "#ffffff", weight: 2, fillColor: "#15803d", fillOpacity: 1 },
  end: { radius: 7, color: "#ffffff", weight: 2, fillColor: "#b91c1c", fillOpacity: 1 },
  // Hollow: it marks what was asked for, not anywhere the route reaches.
  requested: {
    radius: 6,
    color: "#1d4ed8",
    weight: 2,
    fillColor: "#ffffff",
    fillOpacity: 1,
    dashArray: "3 3",
  },
};

/** The nodes `main.ts` hands over, resolved once at startup. */
export interface TrailMapElements {
  readonly map: HTMLElement;
  readonly status: HTMLElement;
  readonly details: HTMLElement;
  readonly title: HTMLElement;
  readonly metrics: HTMLElement;
  readonly notes: HTMLElement;
  readonly notesSection: HTMLElement;
  readonly warnings: HTMLElement;
  readonly warningsSection: HTMLElement;
  readonly unknowns: HTMLElement;
  readonly unknownsSection: HTMLElement;
  readonly attribution: HTMLElement;
}

function toLeaflet(point: LatLng): L.LatLngExpression {
  return [point.lat, point.lng];
}

function clear(element: HTMLElement): void {
  element.replaceChildren();
}

/**
 * One list item per string, as text.
 *
 * `dir="auto"` per item rather than per list: a result can hold a Hebrew
 * description beside an English warning, and the browser resolves each on its
 * own content.
 */
function fillList(list: HTMLElement, entries: readonly string[]): void {
  clear(list);
  for (const entry of entries) {
    const item = document.createElement("li");
    item.dir = "auto";
    item.textContent = entry;
    list.append(item);
  }
}

function fillSection(
  section: HTMLElement,
  list: HTMLElement,
  entries: readonly string[],
): void {
  section.hidden = entries.length === 0;
  fillList(list, entries);
}

/**
 * Label/value pairs, each in its own wrapper so the row can wrap as a unit.
 *
 * A `<div>` around a `dt`/`dd` pair is valid inside a `<dl>`, and it is what
 * keeps a label from wrapping away from its value on a narrow host.
 */
function fillMetrics(container: HTMLElement, model: MapViewModel): void {
  clear(container);
  for (const { label, value } of model.metrics) {
    const pair = document.createElement("div");
    pair.className = "trail-map__metric";
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.dir = "auto";
    detail.textContent = value;
    pair.append(term, detail);
    container.append(pair);
  }
}

function fillAttribution(element: HTMLElement, model: MapViewModel): void {
  clear(element);
  const lines = [model.attribution.notice, ...model.attribution.sources].filter(
    (line) => line.length > 0,
  );
  lines.push(`Basemap tiles: ${TILE_ATTRIBUTION}`);
  for (const line of lines) {
    const paragraph = document.createElement("p");
    paragraph.dir = "auto";
    paragraph.textContent = line;
    element.append(paragraph);
  }
}

/**
 * A button that returns the viewport to the whole route.
 *
 * A real `<button>` in the Leaflet control layer, so it is reachable by Tab and
 * activated by Enter or Space without any key handling of our own. Panning and
 * zooming from the keyboard are Leaflet's, enabled by default.
 */
function fitControl(onFit: () => void): L.Control {
  const control = new L.Control({ position: "topright" });
  control.onAdd = () => {
    const container = L.DomUtil.create("div", "leaflet-bar trail-map__fit");
    const button = L.DomUtil.create("button", "", container) as HTMLButtonElement;
    button.type = "button";
    button.textContent = "Fit route";
    button.title = "Zoom out to the whole route";
    L.DomEvent.disableClickPropagation(container);
    L.DomEvent.on(button, "click", onFit);
    return container;
  };
  return control;
}

/**
 * A Leaflet-backed renderer.
 *
 * The map object is created once and reused: tearing it down per result would
 * refetch every tile, and a host may deliver several results into one view.
 */
export function createTrailMapRenderer(elements: TrailMapElements): MapRenderer {
  const map = L.map(elements.map, {
    // Both default to true; named because they are the accessibility contract.
    keyboard: true,
    zoomControl: true,
    // The document declares one tile origin, so there is nothing to attribute
    // that the footer does not already say — but Leaflet's control is where a
    // reader looks for it on a map.
    attributionControl: true,
  });

  L.tileLayer(TILE_URL, { maxZoom: MAX_ZOOM, attribution: TILE_ATTRIBUTION }).addTo(map);

  /** Everything belonging to the current result, cleared on the next one. */
  const routeLayers = L.layerGroup().addTo(map);
  let routeBounds: L.LatLngBounds | null = null;

  const fitRoute = (): void => {
    if (routeBounds !== null && routeBounds.isValid()) {
      map.fitBounds(routeBounds, { padding: FIT_PADDING });
    }
  };
  fitControl(fitRoute).addTo(map);

  function drawMarker(marker: MapMarker): void {
    L.circleMarker(toLeaflet(marker.position), MARKER_STYLE[marker.role])
      .bindTooltip(marker.label)
      .addTo(routeLayers);
  }

  function draw(model: MapViewModel): void {
    routeLayers.clearLayers();

    // Each part is its own polyline. Concatenating them would draw a leg
    // between two pieces that nothing says are connected on the ground.
    const bounds = L.latLngBounds([]);
    for (const part of model.lineParts) {
      const points = part.map(toLeaflet);
      L.polyline(points, LINE_STYLE[model.kind]).addTo(routeLayers);
      for (const point of points) bounds.extend(point);
    }

    for (const gap of model.gaps) {
      L.polyline([toLeaflet(gap.from), toLeaflet(gap.to)], GAP_STYLE)
        .bindTooltip(gap.label)
        .addTo(routeLayers);
      bounds.extend(toLeaflet(gap.from));
    }

    for (const marker of model.markers) drawMarker(marker);

    routeBounds = bounds;
    fitRoute();
  }

  function showDetails(model: MapViewModel): void {
    elements.title.textContent = model.title;
    elements.details.dataset["kind"] = model.kind;
    fillMetrics(elements.metrics, model);
    fillSection(elements.notesSection, elements.notes, model.notes);
    fillSection(elements.warningsSection, elements.warnings, model.warnings);
    fillSection(elements.unknownsSection, elements.unknowns, model.unknowns);
    fillAttribution(elements.attribution, model);
    elements.details.hidden = false;
  }

  return {
    render(state: ViewState): void {
      if (state.status === "ready") {
        elements.status.textContent = "";
        elements.map.hidden = false;
        draw(state.model);
        showDetails(state.model);
        // The container was hidden or resized while the result arrived; Leaflet
        // sizes tiles from the container and has to be told it changed.
        map.invalidateSize();
        return;
      }

      routeLayers.clearLayers();
      routeBounds = null;
      elements.details.hidden = true;
      // A map with no route has no viewport to show, and an empty grey panel
      // reads as something that failed to load. The status line says what is
      // happening instead.
      elements.map.hidden = true;
      elements.status.textContent =
        state.status === "waiting" ? "Waiting for a route…" : state.reason;
    },
  };
}
