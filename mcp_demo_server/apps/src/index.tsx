/**
 * The entry point for every MCP App this server serves.
 *
 * ONE BUNDLE, FOUR APPS. React and the Apps SDK are most of the weight, and the
 * whole document has to be inlined because the app runs in a sandboxed iframe
 * with an opaque origin and can fetch nothing. Four bundles would mean four
 * copies of that weight in the repository, so all four components ship in one
 * file and the mount node says which to draw:
 *
 *     <div id="root" data-app="rebalance"></div>
 *
 * Everything protocol-shaped happens here, once. A component gets the two props
 * in appProps.ts and never touches the `App` instance.
 */
import { Component, useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { createRoot } from "react-dom/client";

import { useApp } from "@modelcontextprotocol/ext-apps/react";
import type { CallToolResult } from "@modelcontextprotocol/client";

import type { AppComponent, AppData, CallTool } from "./appProps";
import { Projection } from "./projection";
import { Rebalance } from "./rebalance";
import { Signature } from "./signature";
import { Trade } from "./trade";

/** Every app this bundle can draw, keyed by the `data-app` on the mount node. */
const APPS: Record<string, AppComponent> = {
  projection: Projection,
  rebalance: Rebalance,
  signature: Signature,
  trade: Trade,
};

/**
 * The handshake, the tool result and host styling, for whichever app is mounted.
 *
 * `useApp` opens the connection on mount and turns on the SDK's ResizeObserver,
 * so an app reports its own height without asking. The tool-result listener is
 * registered in `onAppCreated`, which runs before `connect`: the host sends the
 * result as soon as the handshake finishes, and a listener attached after that
 * can miss it outright.
 */
function Mount({ name }: { name: string }) {
  const [data, setData] = useState<AppData | null>(null);
  const [args, setArgs] = useState<AppData>({});

  const { app, isConnected, error } = useApp({
    appInfo: { name: "meridian-app", version: "1.0.0" },
    capabilities: { availableDisplayModes: ["inline"] },
    onAppCreated: (created) => {
      // `structuredContent` is the shaped half of a tool result and the half an
      // app draws; `content` is the block list the model reads.
      created.addEventListener("toolresult", (params: CallToolResult) => {
        setData((params.structuredContent ?? {}) as AppData);
      });
      // Both input notifications, because the SDK registers a JSON-RPC handler
      // only for events someone listens to: an unhandled notification is
      // dropped with no warning. The partials are what let an app draw while
      // the model is still writing the arguments, and `toolinput` is the one
      // the spec promises arrives exactly once, at the end.
      const takeArgs = (params: { arguments?: Record<string, unknown> }) =>
        setArgs((params.arguments ?? {}) as AppData);
      created.addEventListener("toolinputpartial", takeArgs);
      created.addEventListener("toolinput", takeArgs);
    },
  });

  // The host may offer its palette during the handshake, and what arrives is
  // applied as custom properties so an app that wants them has them.
  //
  // NOT `useHostStyles`. It also sets `color-scheme` inline, which beats
  // shell.css's `color-scheme: light` and puts the browser's dark range
  // sliders, spinners and select inside 1995 light chrome. It pulls in
  // `useHostFonts` too, which injects the host's font CSS verbatim: an
  // `@import` in there is a network request, and this document has an opaque
  // origin and no network.
  const hostContext = app?.getHostContext();
  const theme = hostContext?.theme;
  const variables = hostContext?.styles?.variables;
  useEffect(() => {
    const root = document.documentElement;
    // `data-theme` and the custom properties, and deliberately NOT
    // `color-scheme`: shell.css declares it `light` and an inline value beats
    // the stylesheet, which would put the browser's dark sliders, spinners and
    // select inside the 1995 light chrome.
    if (theme === "dark") root.setAttribute("data-theme", "dark");
    else if (theme === "light") root.removeAttribute("data-theme");
    for (const [name_, value] of Object.entries(variables ?? {})) {
      if (value) root.style.setProperty(name_, String(value));
    }
  }, [theme, variables]);

  const callTool = useCallback<CallTool>(
    async (tool, args) => {
      if (!app) throw new Error("The app is not connected to its host yet.");

      return app.callServerTool({ name: tool, arguments: args });
    },
    [app],
  );

  // Say what is wrong, or which half is still missing. A blank pane is
  // indistinguishable from a broken one, and the person watching cannot tell
  // whether to wait or to retry. A name with no component is checked first,
  // because it is wrong from the moment the document loads and no amount of
  // waiting fixes it.
  const Component = APPS[name];
  if (!Component) return <p className="msg">No app named {name} is in this bundle.</p>;

  if (error) return <p className="msg">Could not reach the host: {error.message}</p>;

  if (!isConnected) return <p className="msg">Connecting to the host.</p>;

  if (!data) return <p className="msg">Waiting for the tool result.</p>;

  return (
    <Boundary>
      <Component data={data} args={args} callTool={callTool} />
    </Boundary>
  );
}

/**
 * Keeps a throwing app from emptying the client area.
 *
 * `data` is JSON a host forwarded from a server, so a component can be handed
 * a shape it does not expect, and React unmounts the whole subtree when one
 * throws. Before this file the markup was static HTML already in the document
 * and a failed render left the controls on screen; now every control is inside
 * the subtree, so without this the person gets a titled window with an empty
 * client area and no way to tell whether to wait or retry.
 */
class Boundary extends Component<{ children: ReactNode }, { failed: string }> {
  state = { failed: "" };

  static getDerivedStateFromError(err: unknown) {
    return { failed: err instanceof Error ? err.message : String(err) };
  }

  render() {
    if (this.state.failed) {
      return <p className="msg">This app could not draw what it was sent: {this.state.failed}</p>;
    }

    return this.props.children;
  }
}

const node = document.getElementById("root");
if (!node) {
  throw new Error("No #root in the app document. mcp_demo_server/apps.py renders the mount node.");
}

// No StrictMode: its double mount would open the handshake twice, and one
// `ui/initialize` per frame is what the host expects.
createRoot(node).render(<Mount name={node.dataset.app ?? ""} />);
