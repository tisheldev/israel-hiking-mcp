/**
 * The only module that knows this component is running inside an MCP Apps host.
 *
 * It implements `ResultSource` and nothing else. That is what keeps the MCP
 * Apps SDK out of `normalize.ts` and `map.ts` — and what lets their tests run
 * with a three-line fake instead of a host.
 *
 * Only the portable protocol is used: `App`, `ontoolresult`, `connect()`. No
 * `window.openai` and no other host-specific object appears anywhere in this
 * component, so the same document renders in every compliant client.
 *
 * Nothing is called back into the server. This view draws a result that has
 * already been computed; it never re-fetches geometry, and a host that grants
 * no tool-calling permission loses nothing.
 */

import { App } from "@modelcontextprotocol/ext-apps";

import type { ResultSource } from "./types.js";

/**
 * Identifies this view to the host during the `ui/initialize` handshake.
 *
 * The version is the component's, not the server's: it describes what this
 * document can render, and it moves with `TRAIL_MAP_RESOURCE_URI`.
 */
const APP_INFO = {
  name: "israel-hiking-trail-map",
  version: "1.0.0",
} as const;

/**
 * Tool results from the MCP Apps host.
 *
 * The handler is registered before `connect()` — a host may deliver the result
 * that opened this view as soon as the handshake completes, and one registered
 * afterwards can miss it.
 */
export function createHostResultSource(): ResultSource {
  const app = new App(APP_INFO);
  const listeners: ((result: unknown) => void)[] = [];

  app.ontoolresult = (result: unknown) => {
    for (const listener of listeners) listener(result);
  };

  return {
    onResult(listener) {
      listeners.push(listener);
    },
    async start() {
      await app.connect();
    },
  };
}
