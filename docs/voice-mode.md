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

## Setup

```bash
# .env
GEMINI_API_KEY=<from https://aistudio.google.com/apikey>
```

Then create an assistant with `voice: {enabled: true}` in the setup payload, or flip
`metadata.voice.enabled` on an existing one.

## Known gaps

- **No UI to flip the flag.** It is honoured everywhere but only settable in the setup
  payload; the Settings panel already patches assistant metadata live, so a switch there
  is the obvious next step.
- **Not verified against a live session.** Every pure part is unit-tested
  (`voice_test.js`, `test_voice.py`, `test_voice_trace.py`) and the token, trace and
  nesting paths are wired end to end, but nobody has spoken to it yet. The two things
  most likely to need a fix on first contact are the mic downsample and whether a second
  `functionResponse` for the same call id is accepted as readily as the ADK example
  suggests.
- **Session limits.** Resumption is wired (a ~10 minute connection reconnects on its
  handle), but the 15-minute audio-session cap needs `contextWindowCompression` to go
  past, which is not set up yet.
- **Cost is ours.** Everything else in the demo runs on the customer workspace's
  credentials; voice bills to `GEMINI_API_KEY` regardless of whose assistant it is.
