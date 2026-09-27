/**
 * Composition root: the one place concrete things are created.
 *
 * The bridge, the normalizer and the renderer are joined here and nowhere else.
 * Each of them depends only on the narrow interfaces in `types.ts`, so the flow
 * below — result in, view state out, render — can be exercised with a fake
 * source and a fake renderer, with no host and no browser map involved.
 */

import "./styles.css";

import { createHostResultSource } from "./bridge.js";
import { createTrailMapRenderer, type TrailMapElements } from "./map.js";
import { toViewState } from "./normalize.js";
import type { MapRenderer, ResultSource } from "./types.js";

function elementById(id: string): HTMLElement {
  const element = document.getElementById(id);
  if (element === null) {
    throw new Error(`the trail map document is missing #${id}`);
  }
  return element;
}

function resolveElements(): TrailMapElements {
  return {
    map: elementById("map"),
    status: elementById("status"),
    details: elementById("details"),
    title: elementById("title"),
    metrics: elementById("metrics"),
    notes: elementById("notes"),
    notesSection: elementById("notes-section"),
    warnings: elementById("warnings"),
    warningsSection: elementById("warnings-section"),
    unknowns: elementById("unknowns"),
    unknownsSection: elementById("unknowns-section"),
    attribution: elementById("attribution"),
  };
}

/**
 * Wire a source to a renderer.
 *
 * Exported for its own sake: this three-line rule — every result becomes a view
 * state, every view state is rendered — is the whole application logic, and it
 * is worth being able to test it directly.
 */
export function connectRenderer(source: ResultSource, renderer: MapRenderer): void {
  renderer.render({ status: "waiting" });
  source.onResult((result) => renderer.render(toViewState(result)));
}

async function start(): Promise<void> {
  const renderer = createTrailMapRenderer(resolveElements());
  const source = createHostResultSource();
  connectRenderer(source, renderer);
  await source.start();
}

// Bootstrap only when this module was loaded into the real document. The unit
// tests import `connectRenderer` from here, and a composition root that built a
// Leaflet map and dialled a host on import would need both to exist first.
if (document.getElementById("trail-map") !== null) {
  void start();
}
