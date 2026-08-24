// AudioWorklet that captures mic input and ships raw Float32 frames to the main
// thread, which converts them to the 16-bit PCM the Live API wants.
//
// Adapted from langchain-ai/google-adk-realtime-deepagents-example (static/).
// Lives in public/ rather than src/ because addModule() loads it by URL, in its own
// worklet realm: it is never bundled and must not import anything.
class PcmRecorderProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (input && input[0]) {
      // Copy: the engine reuses the underlying buffer after process() returns.
      this.port.postMessage(input[0].slice(0));
    }
    return true; // keep the processor alive
  }
}

registerProcessor("pcm-recorder-processor", PcmRecorderProcessor);
