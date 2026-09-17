/**
 * Render the MCP Apps in a LangGraph thread.
 *
 * Hand it what `useStream` returned and it renders the apps, if there are any.
 * There is no part type to construct, no adapter to write, and no second
 * lookup to tell which tools ship a UI: the thread already contains that, and
 * finding it is this component's job rather than the caller's.
 *
 *     <MCPAppRenderer thread={thread} sandbox={{ url: SANDBOX }}
 *                     loadResource={loadResource} handlers={handlers} />
 *
 * Built on `@modelcontextprotocol/ext-apps`, the extension's official SDK, so
 * the wire format, version negotiation and the ordering rules are its problem.
 */
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { AppBridge, PostMessageTransport } from "@modelcontextprotocol/ext-apps/app-bridge";
import type { McpAppMetadata, McpAppPart, McpAppThread } from "./bindings";
import { toolInputAction } from "./ordering";
import { readDocument, type McpAppResource } from "./documents";
import { useMCPApps } from "./useMCPApps";

/** What the host will do on a view's behalf. A view can do nothing else. */
export interface McpAppHandlers {
  /**
   * Proxy a `tools/call`.
   *
   * SEP-1865 makes refusing a tool whose `visibility` omits `"app"` a MUST, so
   * this is never wired straight to an MCP client: it goes to the host, which
   * checks first.
   */
  callTool?: (params: {
    name: string;
    arguments: Record<string, unknown>;
  }) => Promise<{ content?: unknown[]; structuredContent?: unknown }>;
  /** Read a resource for the view, which has no origin and cannot fetch. */
  readResource?: (params: { uri: string }) => Promise<unknown[]>;
  /** Open a link. Defaults to `window.open` for http and https only. */
  openLink?: (params: { url: string }) => Promise<void>;
  /** Put the view's text into the conversation. */
  onMessage?: (text: string) => void;
  /**
   * The view asked to be shown differently, and the host agreed.
   *
   * Only ever called with a mode the host declared in
   * `hostContext.availableDisplayModes`; a view asking for anything else is
   * refused before this. The host moves the frame, and the resulting mode is
   * returned to the view either way, which is what lets it rely on the answer
   * rather than guess.
   */
  onDisplayMode?: (mode: string) => void;
  /**
   * Tools a view may call, by name. Optional.
   *
   * Set it and a call to anything else never leaves the page. Leave it unset
   * and every call goes to `callTool`, which is the host's server route and
   * the only place the decision actually counts: a browser check is a
   * courtesy to the view, since the page it is defending is the same page a
   * determined caller controls.
   *
   * So this is an optimisation, not the enforcement, and a host that has no
   * list up front should not be made to fetch one. Passing it late is fine:
   * handlers are read at call time rather than captured when the frame is
   * built.
   */
  allowedTools?: string[];
}

/** Everything an app needs that is the same for all of them. */
export interface McpAppConfig {
  /**
   * How the view is isolated. Two shapes, and the choice is a real one.
   *
   * `{ url }` is a sandbox proxy on a DIFFERENT ORIGIN than the host, which
   * is what SEP-1865 requires of a web host. The url is the proxy document's,
   * not any app's. The indirection is what lets a view hold
   * `allow-same-origin` without holding the host's origin, and it is the only
   * place the server's declared CSP can be applied.
   *
   * `{ direct: true }` renders the view straight into an iframe with
   * `allow-scripts` and never `allow-same-origin`, for a host that has only
   * one origin to serve from. That is a deviation from the spec and a
   * STRICTER one: the view gets an opaque origin, so no cookies, no storage,
   * no parent DOM and no network at all. What it costs is any app that
   * genuinely needs same-origin, for ES modules or storage, which will not
   * run. Prefer the proxy where a second origin exists.
   */
  sandbox:
    | {
        url: string | URL;
        /** The view's own sandbox attribute, applied by the proxy. */
        innerSandbox?: string;
        className?: string;
        style?: CSSProperties;
      }
    | { direct: true; className?: string; style?: CSSProperties };
  /** Read the `ui://` document. Usually a route on the host's own backend. */
  loadResource: (app: McpAppMetadata) => Promise<McpAppResource>;
  /**
   * Bindings by tool name, usually read once from the host's own route.
   *
   * Optional, and worth passing. With it an app mounts as soon as the model
   * names the tool, so the arguments reach the view as they stream; without
   * it the app waits for the per-call stamp, which arrives after the
   * arguments are already complete.
   */
  apps?: Record<string, McpAppMetadata>;
  handlers?: McpAppHandlers;
  hostInfo?: { name: string; version: string };
  /** Merged into the context handed to every view. */
  hostContext?: Record<string, unknown>;
  /**
   * The tool's JSON Schema, for `hostContext.toolInfo.tool`.
   *
   * `Tool` declares `inputSchema` as required and the SDK validates the
   * initialize result, so leaving it out is not a cautious partial answer: an
   * app built on that SDK rejects the handshake outright. Absent, this sends
   * `{type: "object"}`, which is a valid empty object schema rather than an
   * invention, but a host that HAS the schema should pass it.
   */
  toolInputSchema?: Record<string, unknown>;
  /** Shown while an app document is being read. */
  fallback?: ReactNode;
}

/** One app, placed wherever the conversation puts it. */
export interface MCPAppProps extends McpAppConfig {
  part: McpAppPart;
}

/**
 * Every app in a thread, rendered together.
 *
 * The one-liner, for a host that has nowhere particular to put them. Anything
 * that interleaves apps with its own message components wants `useMCPApps`
 * and `MCPApp` instead, which is the same machinery without the placement.
 */
export interface MCPAppRendererProps extends McpAppConfig {
  /** Straight from `useStream`. */
  thread: McpAppThread;
}

const DEFAULT_INNER_SANDBOX = "allow-scripts allow-forms";


/** Render every app in the thread. Renders nothing when there are none. */
export function MCPAppRenderer({ thread, ...config }: MCPAppRendererProps) {
  const mcpApps = useMCPApps(thread, { apps: config.apps, loadResource: config.loadResource });
  return (
    <>
      {mcpApps.all.map((part) => (
        <MCPApp key={part.toolCallId} part={part} {...config} />
      ))}
    </>
  );
}

export function MCPApp({
  part,
  sandbox,
  loadResource,
  handlers,
  hostInfo,
  hostContext,
  toolInputSchema,
  fallback,
}: MCPAppProps) {
  const frame = useRef<HTMLIFrameElement | null>(null);
  const [bridge, setBridge] = useState<AppBridge | null>(null);
  const [resource, setResource] = useState<McpAppResource | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [height, setHeight] = useState(340);

  // The mode the view is actually in, which only the host changes.
  const mode = useRef("inline");
  const direct = "direct" in sandbox;
  const proxyUrl = direct ? null : String(sandbox.url);
  const innerSandbox = direct ? null : sandbox.innerSandbox;

  // Read at call time, not captured. A handler set or changed after the frame
  // exists still applies, which is what makes a late `allowedTools` harmless.
  const live = useRef(handlers);
  live.current = handlers;

  // Read once, when the frame is built, so passing them inline (which every
  // caller does) does not rebuild the bridge on every render. A rebuilt
  // bridge tears down a handshake the iframe never repeats, and the app then
  // sits there talking to a host that has forgotten it.
  const config = useRef({ hostInfo, hostContext, toolInputSchema });
  config.current = { hostInfo, hostContext, toolInputSchema };

  /*
   * Effects here key on STRINGS, never on the objects around them.
   *
   * `thread` is a fresh object on every stream frame, so every part and every
   * binding derived from it is fresh too. An effect keyed on `part.app` would
   * therefore re-read the document, replace the resource, and tear down the
   * bridge several times a second, and the only visible symptom is an app
   * that never finishes its handshake.
   */
  const uri = part.app.resourceUri;
  const load = useRef(loadResource);
  load.current = loadResource;

  useEffect(() => {
    let active = true;
    setFailed(null);
    readDocument(uri, load.current).then(
      (found) => active && setResource(found),
      (err) => active && setFailed(String(err?.message ?? err)),
    );
    return () => {
      active = false;
    };
  }, [uri]);

  useEffect(() => {
    const win = frame.current?.contentWindow;
    if (!resource || !win) return;

    const app = new AppBridge(
      // No MCP client. Given one the SDK forwards a view's calls automatically,
      // which applies none of the checks SEP-1865 requires, so the two proxied
      // methods arrive as handlers instead and the host answers them.
      null,
      config.current.hostInfo ?? { name: "langchain-host", version: "0.1.0" },
      {
        openLinks: {},
        logging: {},
        ...(live.current?.callTool ? { serverTools: {} } : {}),
        ...(live.current?.readResource ? { serverResources: {} } : {}),
      },
      {
        hostContext: {
          // A COMPLETE `Tool`. The SDK validates the initialize result and
          // `inputSchema` is required, so omitting it is not a cautious
          // partial answer: an app built on that SDK rejects the handshake.
          toolInfo: {
            tool: {
              name: part.toolName,
              inputSchema: config.current.toolInputSchema ?? { type: "object" },
            },
          },
          displayMode: "inline",
          availableDisplayModes: ["inline"],
          ...config.current.hostContext,
        } as never,
      },
    );
    setBridge(app);

    app.onsizechange = ({ height: h }) => {
      if (typeof h === "number") setHeight(Math.min(Math.max(h, 160), 900));
    };

    app.oncalltool = async (params) => {
      const call = live.current?.callTool;
      const allowed = live.current?.allowedTools;
      if (!call) throw new Error("This host does not proxy tool calls.");
      // Only when the host said which tools are open. Refusing everything
      // when it did not would make an unset allowlist look like a server that
      // denies every tool, which is the opposite of what omitting it means.
      if (allowed && !allowed.includes(String(params.name))) {
        throw new Error(`${params.name} is not open to apps`);
      }

      const out = await call({
        name: String(params.name),
        arguments: (params.arguments ?? {}) as Record<string, unknown>,
      });
      return {
        content: (out.content ?? []) as never,
        structuredContent: out.structuredContent as never,
        isError: false,
      };
    };

    app.onreadresource = async (params) => {
      const read = live.current?.readResource;
      if (!read) throw new Error("This host does not proxy resources/read.");
      return { contents: (await read({ uri: String(params.uri) })) as never };
    };

    app.onopenlink = async (params) => {
      const url = String(params.url ?? "");
      const open = live.current?.openLink;
      if (open) return (await open({ url }), {});
      // `javascript:` would run in THIS page, which is the whole thing the
      // sandbox prevents, so only real links open.
      if (!/^https?:\/\//i.test(url)) throw new Error("Only http and https links open.");
      window.open(url, "_blank", "noopener,noreferrer");
      return {};
    };

    app.onrequestdisplaymode = async (params) => {
      const wanted = String(params.mode ?? "");
      const offered = (config.current.hostContext?.availableDisplayModes as string[]) ?? ["inline"];
      if (offered.includes(wanted)) {
        mode.current = wanted;
        live.current?.onDisplayMode?.(wanted);
      }

      return { mode: mode.current as "inline" | "fullscreen" | "pip" };
    };

    app.onmessage = async (params) => {
      const push = live.current?.onMessage;
      if (!push) throw new Error("This host cannot accept a message right now.");
      const text = String((params.content as { text?: string })?.text ?? "").trim();
      if (!text) throw new Error("ui/message needs content.text.");
      push(text);
      return {};
    };

    // Only in proxy mode. Rendering direct, the document is already the
    // frame's `srcDoc` and there is no proxy to hand it to.
    app.onsandboxready = () => {
      void app.sendSandboxResourceReady({
        html: resource.html,
        sandbox: innerSandbox ?? DEFAULT_INNER_SANDBOX,
        csp: (resource.meta?.csp ?? undefined) as never,
        permissions: (resource.meta?.permissions ?? undefined) as never,
      });
    };

    void app.connect(new PostMessageTransport(win, win));
    return () => {
      // A request, not a notification: the spec has the host give the view a
      // chance to save what the person typed before the frame goes.
      void app.teardownResource({ reason: "The app was closed." }).catch(() => {});
      setBridge(null);
    };
    // Deliberately not keyed on the input or the result. Those change on every
    // streamed frame, and rebuilding would tear down a handshake the iframe
    // never repeats: its document does not reload, so the app would sit there
    // talking to a host that had forgotten it.
    // The resource's own fields are read here but MUST NOT be dependencies:
    // a new resource object on any frame would rebuild the bridge, and the
    // iframe never repeats its handshake, so the app would be left talking to
    // a host that had forgotten it. The uri and html below are the identity
    // that actually matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resource?.uri, resource?.html, part.toolName, innerSandbox, proxyUrl]);

  // The host moved the app itself, so tell the view: it may have its own
  // chrome to put back. Declarative on purpose, so there is no imperative
  // handle to keep in sync with where the frame actually is.
  const wanted = (hostContext?.displayMode as string) ?? "inline";
  useEffect(() => {
    if (!bridge || wanted === mode.current) return;
    mode.current = wanted;
    void bridge.sendHostContextChange({ displayMode: wanted as "inline" | "fullscreen" | "pip" });
  }, [bridge, wanted]);

  useToolInput(bridge, part);

  if (failed) return <div role="alert">Could not load the app: {failed}</div>;
  if (!resource) return <>{fallback ?? null}</>;

  const common = {
    ref: frame,
    title: `MCP app for ${part.toolName}`,
    "aria-label": part.app.resourceUri,
    className: sandbox.className,
    style: sandbox.style ?? { width: "100%", height, border: 0 },
  };

  // Direct: the view itself, with an opaque origin. `allow-same-origin` must
  // never appear here, because this frame is on the HOST's origin and adding
  // it would hand server-authored HTML the host's cookies and DOM.
  if (direct) return <iframe {...common} srcDoc={resource.html} sandbox="allow-scripts" />;

  // Proxy: the PROXY's frame. The view is one further in, created by the
  // proxy on the proxy's origin, which is the point of the indirection.
  // `allow-scripts` with `allow-same-origin` normally defeats the sandbox,
  // because the frame can reach back into the page that embedded it. Here it
  // cannot: this frame is the sandbox PROXY, served from an origin that is
  // not the host's, so the origin it keeps is its own. That separation is
  // what SEP-1865 requires of a web host, and granting same-origin against
  // the proxy's origin is the entire reason the proxy exists. The view
  // itself, one frame further in, is sandboxed by the proxy.
  // oxlint-disable-next-line iframe-missing-sandbox
  return <iframe {...common} src={proxyUrl!} sandbox="allow-scripts allow-same-origin allow-forms" />;
}

/**
 * Feed the call to the view as it arrives.
 *
 * This is the part worth having. SEP-1865 defines `tool-input-partial` so a
 * view can draw while the model is still writing the arguments, and a host
 * that only ever sends the final `tool-input` leaves every app blank until the
 * call is complete. LangGraph's messages stream parses partial JSON as it
 * goes, so the arguments really do arrive in pieces and there is something to
 * forward.
 *
 * The ordering the spec fixes and this keeps: zero or more partials, then
 * exactly one `tool-input`, then nothing. A run that finished between renders
 * still sends its one final input, so a view never waits on a partial that
 * already happened.
 */
function useToolInput(bridge: AppBridge | null, part: McpAppPart) {
  const [ready, setReady] = useState(false);
  const sentFinal = useRef(false);
  const sentResult = useRef(false);

  // A host MUST NOT send anything before the view says `initialized`. Sending
  // early is silent: the view simply never draws.
  useEffect(() => {
    setReady(false);
    sentFinal.current = false;
    sentResult.current = false;
    if (!bridge) return;

    const onReady = () => setReady(true);
    bridge.addEventListener("initialized", onReady);
    return () => bridge.removeEventListener("initialized", onReady);
  }, [bridge]);

  const input = JSON.stringify(part.input);
  useEffect(() => {
    if (!bridge || !ready) return;

    const action = toolInputAction({
      ready,
      sentFinal: sentFinal.current,
      streaming: part.streaming,
    });
    if (action === "partial") {
      void bridge.sendToolInputPartial({ arguments: part.input });
    } else if (action === "final") {
      // Exactly one, and nothing after it. A run that finished before the
      // view was ready still gets its final input, so a view never waits on a
      // partial that already happened.
      sentFinal.current = true;
      void bridge.sendToolInput({ arguments: part.input });
    }

    if (part.output && !sentResult.current) {
      sentResult.current = true;
      void bridge.sendToolResult(part.output as never);
    }
    // `ready` is load-bearing here: `initialized` often arrives after the
    // result does, and without it this never re-runs to send anything.
    //
    // `part.input` is deliberately absent: it is a new object on every frame,
    // so depending on it would send a partial per render rather than per
    // actual change. `input` is its JSON, which changes only when it does.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bridge, ready, input, part.streaming, part.output]);
}
