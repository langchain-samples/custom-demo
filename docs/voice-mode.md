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

## Who the shell thinks it is

`voiceInstructions(persona)` builds the system instruction from the assistant's own
metadata: display name, customer, industry, and the quick-action labels (which carry both
the personas it serves and their topics, in a few words each).

This is easy to miss because the DEEP AGENT's prompt is already customer-specific - the
shell is a separate model, and without an identity of its own it introduces itself as a
generic "analytics assistant" that cannot say whose data it is looking at. Which is the
wrong first impression in a customer demo.

With it, "who are you?" gets answered from a live session as: *"I'm Progressive GPT, your
analytics assistant for Progressive. I can help you with questions about claims, renewals,
or pretty much anything about your customers' data."*

Kept short on purpose - this is a realtime speech model, and a long system instruction costs
latency and adherence - and it degrades to a sayable sentence when an assistant has no
metadata yet.

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

## Narrating a 60-second run

A deep agent turn is far too long to sit through in silence, so the shell sends three
kinds of thing, and only the first is a `toolResponse`:

| When | Sent as | Why |
|---|---|---|
| Immediately | `toolResponse`, `willContinue: true`, `scheduling: SILENT` | Ends the model's wait so it keeps the floor. `willContinue` says the answer is still coming. |
| Every few seconds | `clientContent` user turn, `[system] progress on ...` | Narrates what the agent is ACTUALLY doing, from its own tool calls (`progressLabel`), throttled to one line per 6s. |
| When the run lands | `clientContent` user turn, `[system] result for ...` | The answer, phrased so the model reports it rather than re-answering. |

The trap: **a second `toolResponse` for an answered call is dropped, and so is a
`clientContent` turn carrying a `functionResponse` part** - which is what the ADK example
sends. Verified live on 3.1: the model acknowledges, the result arrives, and it never speaks
again. A plain user turn is narrated every time, and the `[system]` prefix is what stops the
model answering it as though the user had said it.

Live that reads as: *"Let me pull that up." / "I'm still working on that claim status." /
"I'm searching the data for it now." / "I'm getting the dashboard ready." / "...in review
right now, with an inspection scheduled for Friday. You can see the details on your
dashboard."*

TRIED AND REVERTED: asking for a thinking sound ("mmhm", "okaaay") on alternate beats instead
of a line. The model does it readily - there is no API feature for disfluencies, it is purely
a prompt behaviour - but full lines read better, because every beat that is only a sound is a
beat that does not say what the agent is doing.

What is NOT achievable: backchannelling while the USER talks. Gemini stays silent during
your turn by design, and anything else would fight barge-in. The filler covers the agent's
working time, which is where the dead air actually is.

## One trace per conversation

`voice_trace.py` opens a `voice_session` run and hangs the utterances and the tool call
under it:

```
voice_session
  |- user_speech / agent_speech      <- Live's input/output transcription
  `- invoke_deep_agent (tool)        <- outputs carry the agent run's id + URL
```

The root run carries the conversation as a stereo WAV attachment (user left, assistant
right), which is what makes the trace PLAYABLE. Two metadata keys decide whether it renders
as a player or as a file card: `ls_modality: audio` marks the run as a conversation, and
`thread_id` puts it in a THREAD - the turn-by-turn view is where the scrubbable player lives.
Miss the second and you get exactly what shipped first: a `conversation WAV 5MB` card on an
isolated run, with no way to hear it in place. It is the same thread the agent runs on, so the
spoken conversation and the agent's turns are one object. The browser builds the mix
(`lib/voiceRecorder.ts`) and sends it base64 with the closing call. The placement rule is
the subtle part: model audio arrives in bursts faster than real time, so each chunk starts
at the LATER of its arrival time and the end of the previous one - place them at arrival
time and a burst overlaps into a garbled blip, while a genuine pause still leaves silence.

**The agent run is NOT nested inside that span, and cannot be from here.** LangSmith documents the way
to do it - send `langsmith-trace`, read it off `configurable`, wrap the run in
`tracing_context(parent=...)` - and measured against this deployment every form of it loses
the run outright: not nested, not at its own root, nothing recorded, and no error, because
ingestion is asynchronous. A control run without the header traces normally, and the same
parent handle nests correctly when the child is a plain `@traceable` in one process. What
refuses it is the Agent Server's own run identity, which does not defer to an inbound parent.

An unnested trace is cosmetic; a missing trace is the demo. So the tool span records the
agent run's id and resolved URL (`agent_trace` in its outputs) and the agent run traces
normally in the same project: two trees, one click apart. `graph.py` carries the same note,
because the docs make re-adding that parent look obviously correct.

Tested on Agent Server **0.11.1 and 0.13.0** - the newer build behaves identically, so this
is not a version lag waiting to be fixed by an upgrade. It fails in two distinct ways, which
is worth knowing before anyone files it:

| Parent handle | Outcome |
|---|---|
| ROOT-level (the documented shape: traced Python caller, SDK or RemoteGraph, `rt.to_headers()`) | Run is KEPT and shares the caller's trace id, but is not nested - it lands as a second root in that trace. |
| NON-ROOT (what voice needs: the `invoke_deep_agent` tool span) | Run is LOST. Not nested, not at its own root, not anywhere - and no error, because ingestion is asynchronous. |

A control run without the header traces normally, and the same handle nests correctly when
the child is a plain `@traceable` in one process.

**Why the ADK example nests and this does not.** It invokes the deep agent IN PROCESS -
`agent.ainvoke(...)` inside `tracing_context(parent=tool_run)` - so the parent is an
ordinary in-process one and nesting is automatic. Ours runs through Agent Server, in another
process, which is the only reason distributed tracing is involved at all.

Getting the same tree means invoking the agent in-process for voice turns: a route that
runs `build_agent()` inside the tool span (`assistant_evals.py` already does exactly this
for experiments) and streams frames in the shape `ChatPanel` parses, so the dashboard keeps
working. That is a scoped piece of work rather than a tweak - it duplicates streaming
behaviour the platform currently provides - and it is the right next step if the nested tree
matters more than the extra surface.

## Four things the docs will not tell you

All four present identically: the socket opens, closes again, and the button goes back to
idle. No error frame, nothing useful in the network tab. Each one cost a live session to
find, so they are pinned by tests.

0. **Mint the token after the mic, not before.** A token only opens a session for ~60
   seconds (`newSessionExpireTime`), and `getUserMedia` can sit on a permission dialog for
   longer than that, so minting first turns a slow "Allow" into a `1007` that blames the
   token. The session mints its own token from `getToken`, after `startAudio`, and retries
   once with a fresh one if a close reason mentions the token at all.
1. **Send mic audio as `audio`, not `mediaChunks`.** Most examples still show
   `realtimeInput.mediaChunks`, and the server answers `1007 realtime_input.media_chunks is
   deprecated. Use audio, video, or text instead`. It fires on the first frame a microphone
   produces, so nothing short of a real mic finds it.
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

## The voice view

For a voice-enabled assistant the chat rail is replaced by `VoiceStage`: one orb, a status
line, and the assistant's own branding. The dashboard keeps its pane beside it, which is
the whole point - you talk, and the figures appear over there.

The orb's halo tracks MEASURED loudness, not a timer: `frameLevel` takes the RMS of every
audio frame in both directions (your microphone and the model's speech), the session reports
it through `onLevel`, and `VoiceOrb` reads it from an animation frame and writes it to a CSS
custom property. Two consequences worth keeping: the level is a REF rather than React state
(it updates at audio rate, and state would re-render the tree dozens of times a second), and
the easing is asymmetric - fast attack, slow release - which is what makes it read as
breathing rather than flickering. Each layer owns exactly ONE transform, which is not a
style preference: the sphere's drift animation and an audio-driven scale on the same element
means the animation wins and the orb never moves with the voice (it did, for a while). Hence
the body wrapper.

BOTH layers move, in opposite directions per side: sphere
and halo swell outward on the model's speech (the assistant projecting), and draw inward
from a smaller baseline on the user's (listening, leaning in rather than talking over).
Whoever is louder wins, so crosstalk resolves instead of fighting, and neither collapses to
nothing - an orb that vanishes reads as broken. That geometry is `orbTransform`, a pure
function with tests, because "what the orb does" should not be checkable only by talking
to it.

One CSS detail: the sphere's drift keyframes own `transform`, so the audio-driven scale
multiplies INTO the keyframes (`scale(calc(1.04 * var(--sphere-scale)))`) rather than
fighting them for the property - set it separately and the animation simply wins. Colours are DERIVED from the brand hue rather than taken
straight from it: a brand secondary can legitimately be near-black (Progressive's is), and
mixing that into a sphere reads as a bruise, so each stop is lightened toward white and
pulled toward violet or pink. The result is recognisably the customer's colour and reliably
luminous.

The stage shows STATUS only, no transcript: reading is what the chat view is for, and a live
caption pulls the eye off the dashboard. The utterances are still recorded - they are what
make the conversation legible in its trace.

Three more things are load-bearing rather than decorative:

- **ChatPanel stays mounted underneath.** The stage is an overlay, not a swap. ChatPanel
  owns the run machinery and the widget flushing, so unmounting it would take the dashboard
  with it and drop the session's `ask` handle mid-turn.
- **The activity line.** A deep agent turn runs up to a minute; a silent orb with no status
  is indistinguishable from a hung one. It shows the agent's current tool
  (`onActivity`, unthrottled - unlike the spoken narration).
- **Quick actions are a teleprompter.** Tapping one opens the question to READ ALOUD rather
  than submitting it. Sending it as text would make the orb answer something the microphone
  never heard. It collapses on an outside click and on the next finished user turn
  (`userTurns`), since once the question has been read the card is covering the orb.
- **The orb only appears once a session is live.** Before the first tap there is no audio to
  react to, so an orb would be animating over nothing and implying it is already listening;
  a plain mic button says "not yet" honestly.

The X returns to the chat view without hanging up; a compact control then appears in the
header showing state plus the activity line, with a way back. The view resets to the orb when
the active assistant changes, so the choice is per assistant rather than sticky for the tab.

**New Chat restarts a live session.** It has to: the thread is gone, so a session left
running would remember a conversation the agent no longer has, reference figures that exist
nowhere, and keep appending to a trace whose conversation ended. Restarting gives a new
thread, a new Live session and a new `voice_session` trace - and stopping flushes the
finished conversation's audio onto its own trace on the way out.

## Choosing the colours

`orbPalette` (lib/orbPalette.ts) derives three stops from the brand colour in OKLCH:

1. brand colour to OKLCH, and pick the CHROMATIC seed - a surprising number of brands keep a
   near-black as their primary and the real colour as the secondary (Progressive's own seeds
   are `#2d2d2d` and `#0077b3`, and Nike, Apple and Vizient are the same shape), so an
   achromatic primary borrows the secondary's hue rather than a house one
2. clamp L and C into a luminous band, which is what stops a near-black seed reading as a
   storm cloud and a saturated one as a headlight
3. derive the other stops by ROTATING hue, never by mixing toward a fixed colour
4. an almost-white base carrying a trace of the hue, so gaps between stops read as light
5. give the third stop to the brand's real secondary when its hue is far enough away to add
   something (Walmart's yellow earns it; Home Depot's white does not)

Rotation is the load-bearing choice. The first version mixed toward a fixed violet and pink
with `color-mix(in srgb, ...)`, and interpolating two hues in sRGB cuts a chord near the grey
axis: where the stops overlapped they desaturated, which is why a blue brand grew a grey
bruise. Rotating in a perceptual space travels AROUND the axis and cannot.

Progressive comes out `#64c1ff / #9cbeff / #32cbeb` on a near-white blue base. The corpus test
(`orb_palette_test.js`) asserts the whole claim over this repo's real brand seeds plus the
awkward shapes: every stop luminous, chromatic, and at least 15 degrees from its neighbours.

## Choosing the voice

`DEFAULT_VOICE` is **Schedar** ("even" in Google's descriptors) - the upbeat voices read as
breezy when someone is asking about a theft claim. Per assistant it is
`metadata.voice.voice_name`, edited under Settings -> Advanced brand, where the dropdown has
a play button: the samples in `frontend/public/voice-samples/` are pre-rendered by the Live
API (~14KB each), so previewing costs no tokens.

`speechConfig` goes inside `generationConfig`. On `setup` it is rejected with
`1007 Unknown name "speechConfig" at 'setup'`, which - like every other setup mistake -
presents as a socket that just closes.

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
  not run is the last mile in a browser: real microphone capture and the playback worklet.
  The frame shape and the downsample are exercised against a live session (750 frames of a
  generated tone, accepted), and a text turn drives the full tool round trip, so what is
  left is whether a real voice is understood and whether the queued audio plays back
  cleanly. Also unproven: whether a second `functionResponse` for the same call id is
  accepted as readily as the ADK example suggests.
- **Session limits.** Resumption is wired (a ~10 minute connection reconnects on its
  handle), but the 15-minute audio-session cap needs `contextWindowCompression` to go
  past, which is not set up yet.
- **Cost is ours.** Everything else in the demo runs on the customer workspace's
  credentials; voice bills to `GEMINI_API_KEY` regardless of whose assistant it is.
- **`.dockerignore` does not exclude `.env`.** Unrelated to voice, but `langgraph deploy`
  builds from the working tree, so a deploy from a developer's machine ships whatever keys
  are in their local `.env` into the image. Worth fixing before anyone deploys by hand.
