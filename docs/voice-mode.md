# Voice mode (spike)

Talk to a customer's assistant out loud, with the dashboard filling in as it answers.

Off by default and per-assistant: the builder sets `metadata.voice.enabled`, and without
`GEMINI_API_KEY` the mic button never appears at all. Nothing about the agent, its tools
or its prompt changes when it is on.

## The shape

```
 ┌─────────┐  16k PCM  ┌──────────────┐   tool call    ┌──────────────┐
 │ browser │ ────────▶ │ Gemini Live  │ ─────────────▶ │  the SPA     │
 │ mic/spkr│ ◀──────── │  (direct WS) │ ◀───────────── │  (this repo) │
 └─────────┘  24k PCM  └──────────────┘  prose + digest└──────┬───────┘
                                                              │ runStream
                                                       ┌──────▼───────┐
                                                       │  deep agent  │  ← the normal run
                                                       │  + widgets   │
                                                       └──────────────┘
```

Gemini Live owns the **conversation**: microphone, voice activity detection, barge-in,
speech. It gets two capabilities, `invoke_deep_agent` and `resume_deep_agent`, and the
SPA implements both by starting an ordinary streaming run.

That last part is the whole design. **The tool runs in the browser, not on the server.**
The dashboard is built client-side from `push_widget` args as they stream
(`ChatPanel.tsx`), so a server-side implementation would produce a perfect agent run, a
perfect trace, and a blank canvas. Running it in the browser means widgets, chips,
transcript and trace all come from the code path a typed question already uses.

The trade against the [ADK
example](https://github.com/langchain-ai/google-adk-realtime-deepagents-example), which
runs the same split server-side: we give up a Python-side voice loop (and the neat
stereo-WAV recording it can attach) to keep the canvas alive. The audio worklets in
`frontend/public/` are adapted from it.

## Not going silent

A deep agent run takes tens of seconds. The fix is a **two-phase response**: acknowledge
the call immediately (`SILENT` scheduling) so the model keeps the floor and the user can
keep talking, then send the real answer later with `INTERRUPT` scheduling
(`VoiceSession.runTool`).

This is also why the model choice is free. Google documents `behavior: NON_BLOCKING` as
2.5-only, and 3.1's function calling as synchronous only — but the two-phase response
needs no model support, so both work. The flag is sent only where it is supported
(`supportsNonBlocking`). Default is `gemini-3.1-flash-live-preview`; set
`DASHBOARD_VOICE_MODEL` to switch.

## What it speaks

Prose only, never widget JSON. Because the prompt deliberately keeps figures in the
widgets and the prose short, the SPA also sends an `on_screen` digest built from the
flushed widget titles and values (`spokenResult`) — otherwise an eyes-free listener hears
"units fell across three SKUs" and never hears the -12%.

An `ask_user` pause comes back as `needs_approval` with the question and its options
read out; the spoken choice returns through `resume_deep_agent`. The options have to be
spoken, because the run only accepts one of them.

## One trace per conversation

`voice_trace.py` opens a `voice_session` run and hangs the utterances and the tool call
under it:

```
voice_session
  ├─ user_speech / agent_speech      ← Live's input/output transcription
  └─ invoke_deep_agent (tool)
       └─ the agent run              ← nests via distributed tracing
```

The nesting is not automatic, because the run is started by the browser. `open_tool`
returns the `langsmith-trace` / `baggage` headers for the tool span, the SPA puts them on
the run request, and `graph.py` turns them back into a tracing parent. Both ends have to
opt in — if you change one, change the other.

## Four things the docs will not tell you

All four present identically: the socket opens, closes again, and the button goes back to
idle. No error frame, nothing useful in the network tab. Each one cost a live session to
find, so they are pinned by tests.

1. **Use the `BidiGenerateContentConstrained` RPC.** An ephemeral token on the plain
   `BidiGenerateContent` is refused with `1008 Method doesn't allow unregistered callers`.
2. **Pass the token as `access_token`, not `key`.** It replaces an API key but is not one;
   `?key=` gets `1007 Missing or malformed auth token`.
3. **Do not pin the session into the token.** `bidiGenerateContentSetup` + an empty
   `fieldMask` is what the guide invites, and it closes with `1011 Internal error
   encountered` (and a client that leans on it with `setup: {}` gets `1007 token-based
   requests cannot use project-scoped features`). The token carries `uses: 1` and nothing
   else; the session config lives in `voice.ts`.
4. **State `responseModalities` in the client setup.** Omitting it is another bare
   `1011`. The transcription configs belong there too, and they are the only text a
   native-audio session emits.

Also: the guide's `liveConnectConstraints` / `lockAdditionalFields` are SDK field names.
The REST resource wants `bidiGenerateContentSetup` / `fieldMask` (see the v1beta discovery
document's `AuthToken`), and posting the SDK names is a 400.

## Setup

```bash
# .env
GEMINI_API_KEY=<from https://aistudio.google.com/apikey>
```

Then create an assistant with **Voice mode** switched on in the New customer demo dialog,
or flip `metadata.voice.enabled` on one that already exists.

The flag travels SPA -> `SetupInput.voice` -> the setup graph's `_INPUT_KEYS` ->
`prepare_assistant` -> `metadata.voice`. That graph silently drops any input key it does
not list, so a switch can be wired all the way through the UI and still arrive as "off"
with nothing visibly broken. There is a test pinning that link.

## Known gaps

- **The switch is create-time only.** "Voice mode" sits in the New customer demo dialog
  next to "Backfill demo traffic". Turning it on for an assistant that already exists
  means patching `metadata.voice.enabled`; the Settings panel already patches assistant
  metadata live, so a toggle there is the obvious next step.
- **The audio path is unverified.** A real session has been driven end to end from Node
  (mint -> connect -> speak -> `invoke_deep_agent` called with the question passed
  through), so the protocol, the token and the tool declarations are known good. What has
  not run is the browser half: the mic downsample, the playback worklet, and whether a
  second `functionResponse` for the same call id is accepted as readily as the ADK example
  suggests. Those are the first three places to look if a real conversation misbehaves.
- **Session limits.** Resumption is wired (a ~10 minute connection reconnects on its
  handle), but the 15-minute audio-session cap needs `contextWindowCompression` to go
  past, which is not set up yet.
- **Cost is ours.** Everything else in the demo runs on the customer workspace's
  credentials; voice bills to `GEMINI_API_KEY` regardless of whose assistant it is.
