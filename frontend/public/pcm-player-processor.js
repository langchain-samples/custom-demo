// AudioWorklet that plays streamed PCM from the model. The main thread pushes
// Float32 chunks via port.postMessage; we queue and drain them sample by sample so
// playback stays gapless. A "clear" message flushes the queue, which IS barge-in:
// when Gemini reports the user interrupted, we stop mid-sentence instead of talking
// over them for another few seconds.
//
// Adapted from langchain-ai/google-adk-realtime-deepagents-example (static/).
class PcmPlayerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = []; // Float32Array chunks, in arrival order
    this.readIndex = 0; // read offset into queue[0]
    // Whether we were draining audio on the previous render quantum. The main thread
    // needs to know when the model is AUDIBLE, which is not when its audio arrives:
    // Gemini streams an utterance faster than realtime, so the socket goes quiet
    // seconds before the speaker does. Only transitions are posted, so this costs one
    // message per utterance rather than one per quantum.
    this.wasPlaying = false;
    this.port.onmessage = (event) => {
      if (event.data === "clear") {
        this.queue = [];
        this.readIndex = 0;
        return;
      }
      this.queue.push(event.data);
    };
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!out) return true;
    for (let i = 0; i < out.length; i++) {
      if (!this.queue.length) {
        // Underrun: emit silence rather than stalling, so the node keeps pulling.
        out[i] = 0;
        continue;
      }
      const chunk = this.queue[0];
      out[i] = chunk[this.readIndex++];
      if (this.readIndex >= chunk.length) {
        this.queue.shift();
        this.readIndex = 0;
      }
    }
    const playing = this.queue.length > 0;
    if (playing !== this.wasPlaying) {
      this.wasPlaying = playing;
      this.port.postMessage(playing ? "playing" : "drained");
    }
    return true;
  }
}

registerProcessor("pcm-player-processor", PcmPlayerProcessor);
