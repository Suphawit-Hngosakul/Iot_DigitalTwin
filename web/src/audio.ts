import { CFG } from "./api";

const INDEX_TABLE = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8];
const STEP_TABLE = [
  7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118,
  130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963, 1060,
  1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484,
  7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
];

export class LiveAudioPlayer {
  private ctx: AudioContext;
  private gain: GainNode;
  private mockSrc: AudioBufferSourceNode | null = null;
  private predictor = 0;
  private stepIndex = 0;
  private nextPlayTime = 0;

  frames = 0;
  lost = 0;
  lastHeader: { type: number; version: number; samples: number; session: number; seq: number; bytes: number } | null = null;

  constructor() {
    const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    this.ctx = new AudioCtx();
    this.gain = this.ctx.createGain();
    this.gain.gain.value = CFG.mock ? 0.1 : 0.85;
    this.gain.connect(this.ctx.destination);

    if (CFG.mock) {
      const len = this.ctx.sampleRate * 2;
      const buf = this.ctx.createBuffer(1, len, this.ctx.sampleRate);
      const d = buf.getChannelData(0);
      let last = 0;
      for (let i = 0; i < len; i++) {
        last = (last + 0.02 * (Math.random() * 2 - 1)) / 1.02;
        d[i] = last * 3.5;
      }
      this.mockSrc = this.ctx.createBufferSource();
      this.mockSrc.buffer = buf;
      this.mockSrc.loop = true;
      this.mockSrc.connect(this.gain);
      this.mockSrc.start();
    }
  }

  updateMockLevel(dbSpl?: number) {
    if (CFG.mock && dbSpl != null) {
      this.gain.gain.value = Math.min(0.35, Math.max(0.01, (dbSpl - 50) / 120));
    }
  }

  onFrame(buf: ArrayBuffer) {
    if (buf.byteLength < 12) return;
    const dv = new DataView(buf);
    const type = dv.getUint8(0);
    const version = dv.getUint8(1);
    const samples = dv.getUint16(2, true);
    const session = dv.getUint32(4, true);
    const seq = dv.getUint32(8, true);
    this.frames++;
    this.lastHeader = { type, version, samples, session, seq, bytes: buf.byteLength };

    if (!CFG.mock && type === 0x01 && buf.byteLength > 12) {
      const pcm = this.decodeImaAdpcm(new Uint8Array(buf, 12), samples);
      this.schedulePcm(pcm, 16000);
    }
  }

  private decodeImaAdpcm(payload: Uint8Array, expectedSamples: number): Float32Array<ArrayBuffer> {
    const out = new Float32Array(new ArrayBuffer(Math.min(expectedSamples, payload.length * 2) * 4));
    let outIdx = 0;
    for (let i = 0; i < payload.length && outIdx < out.length; i++) {
      const byte = payload[i];
      for (let shift = 0; shift <= 4 && outIdx < out.length; shift += 4) {
        const nibble = (byte >> shift) & 0x0f;
        const step = STEP_TABLE[this.stepIndex];
        let diff = step >> 3;
        if (nibble & 1) diff += step >> 2;
        if (nibble & 2) diff += step >> 1;
        if (nibble & 4) diff += step;
        if (nibble & 8) diff = -diff;

        this.predictor = Math.max(-32768, Math.min(32767, this.predictor + diff));
        this.stepIndex = Math.max(0, Math.min(88, this.stepIndex + INDEX_TABLE[nibble]));
        out[outIdx++] = this.predictor / 32768;
      }
    }
    return out;
  }

  private schedulePcm(pcm: Float32Array<ArrayBuffer>, sampleRate: number) {
    const audioBuf = this.ctx.createBuffer(1, pcm.length, sampleRate);
    audioBuf.copyToChannel(pcm, 0);
    const src = this.ctx.createBufferSource();
    src.buffer = audioBuf;
    src.connect(this.gain);

    const now = this.ctx.currentTime;
    if (this.nextPlayTime < now || this.nextPlayTime > now + 0.6) {
      this.nextPlayTime = now + 0.25; // Jitter buffer 250 ms
    }
    src.start(this.nextPlayTime);
    this.nextPlayTime += audioBuf.duration;
  }

  stop() {
    this.mockSrc?.stop();
    this.ctx.close();
  }
}