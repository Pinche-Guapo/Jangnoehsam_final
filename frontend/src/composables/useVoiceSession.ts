import { computed, readonly, ref } from "vue";
import {
  endChatSession,
  sendChat,
  startChatSession,
  uploadSessionAudio,
  type ChatMetaPayload,
} from "@/api/chat";
import { useAuthStore } from "@/stores/auth";

type VoiceState = "idle" | "speaking" | "listening" | "processing" | "cooldown";
type SessionEndReason = "manual_stop" | "target_reached" | "reset";
type DialogStatePayload = Record<string, unknown> | null;
type ConversationPhase = "opening" | "warmup" | "dialog" | "closing";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
}

type SpeechRecognitionResultEvent = Event & {
  results: SpeechRecognitionResultList;
};

type SpeechRecognitionErrorEvent = Event & {
  error?: string;
};

interface SpeechRecognitionLike extends EventTarget {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((event: SpeechRecognitionResultEvent) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null;
  onend: ((event: Event) => void) | null;
  start: () => void;
  stop: () => void;
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

const SESSION_LIMIT_MS = 300_000;
const SESSION_LIMIT_SEC = 300;
const WARMUP_END_SEC = 45;
const SOFT_WRAP_START_SEC = 240;
const HARD_WRAP_BUFFER_SEC = 20;
const LISTEN_RETRY_DELAY_MS = 350;
const PROFILE_ID_STORAGE_KEY = "voice_profile_id";
const SPEECH_SILENCE_COMMIT_MS = 1300;
const INCOMPLETE_SILENCE_EXTRA_MS = 1800;
const INCOMPLETE_SILENCE_RETRY_LIMIT = 2;
const POST_SPEAK_INPUT_GRACE_MS = 800;
const STT_NO_SPEECH_RETRY_LIMIT = 1;
const NO_SPEECH_GRACE_RETRIES = 1;
const NO_SPEECH_RETRY_DELAY_MS = 700;
const MIC_LEVEL_SENSITIVITY = 6.4;
const MIC_LEVEL_SILENCE_FLOOR = 0.022;
const MIC_ACTIVE_THRESHOLD = 0.024;
const SPEAKING_ACTIVE_THRESHOLD = 0.045;
const SPEAKING_LEVEL_INTERVAL_MS = 50;
const DEFAULT_CONVERSATION_MODE = "mixed";
const SESSION_AUDIO_TIMESLICE_MS = 1000;
const EMERGENCY_RESPONSE_FALLBACK =
  "지금 연결이 잠시 불안정해요. 잠깐 후 다시 시도해 주세요.";
const TIMEOUT_CLOSING_FALLBACK =
  "시간이 거의 다 되어 오늘 대화는 여기서 마무리할게요. 다음에 이어서 이야기해요.";

const DEFAULT_MODEL_RESULT: Record<string, unknown> = {
  stage: "경도 인지저하 의심 단계",
  main_region: "측두엽",
  risk_level: "중간",
  trend: "최근 수개월 동안 큰 변화 없이 유지",
  recommended_training: ["기억 회상", "언어 유창성"],
};

const state = ref<VoiceState>("idle");
const messages = ref<ChatMessage[]>([]);
const currentTranscript = ref("");
const currentResponse = ref("");
const sessionStartTime = ref<Date | null>(null);
const isSessionActive = ref(false);
const sessionId = ref<string | null>(null);
const dialogState = ref<DialogStatePayload>(null);
const turnIndex = ref(0);
const voiceLevel = ref(0);
const isVoiceActive = ref(false);

const conversationPhase = ref<ConversationPhase>("opening");

let sessionTimer: number | null = null;
let softWrapTimer: number | null = null;
let activeUtterance: SpeechSynthesisUtterance | null = null;
let resolveSpeaking: (() => void) | null = null;
let sessionToken = 0;
let recognition: SpeechRecognitionLike | null = null;
let isRecognitionStopping = false;
let silenceCommitTimer: number | null = null;
let stableTranscriptBuffer = "";
let interimTranscriptBuffer = "";
let micStream: MediaStream | null = null;
let micAudioContext: AudioContext | null = null;
let micAnalyser: AnalyserNode | null = null;
let micSourceNode: MediaStreamAudioSourceNode | null = null;
let micWaveData: Uint8Array<ArrayBuffer> | null = null;
let micMeterRaf: number | null = null;
let micMeterToken = 0;
let speakingLevelTimer: number | null = null;
let speakingLevelPhase = 0;
let noSpeechRetryCount = 0;
let sttNoSpeechRetryCount = 0;
let incompleteCommitRetryCount = 0;
let timeoutCloseInProgress = false;
let sessionRecorder: MediaRecorder | null = null;
let sessionRecorderStream: MediaStream | null = null;
let sessionAudioChunks: Blob[] = [];
let sessionAudioMimeType = "";
const authStore = useAuthStore();

const generateId = () =>
  `msg_${Date.now()}_${Math.random().toString(36).slice(2)}`;

const generateSessionId = () =>
  `session_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;

const getOrCreateProfileId = () => {
  if (typeof window === "undefined" || !window.localStorage) return undefined;

  const existing = window.localStorage.getItem(PROFILE_ID_STORAGE_KEY)?.trim();
  if (existing) return existing;

  const next = `profile_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
  window.localStorage.setItem(PROFILE_ID_STORAGE_KEY, next);
  return next;
};

const pickSessionAudioMimeType = () => {
  if (typeof window === "undefined" || typeof MediaRecorder === "undefined") {
    return "";
  }
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/ogg;codecs=opus",
  ];
  for (const mimeType of candidates) {
    if (
      typeof MediaRecorder.isTypeSupported === "function" &&
      MediaRecorder.isTypeSupported(mimeType)
    ) {
      return mimeType;
    }
  }
  return "";
};

const extensionFromMimeType = (mimeType: string) => {
  const normalized = mimeType.toLowerCase();
  if (normalized.includes("wav")) return "wav";
  if (normalized.includes("mp4") || normalized.includes("m4a")) return "m4a";
  if (normalized.includes("ogg")) return "ogg";
  if (normalized.includes("mpeg") || normalized.includes("mp3")) return "mp3";
  return "webm";
};

const downmixToMono = (audioBuffer: AudioBuffer) => {
  const channelCount = Math.max(1, audioBuffer.numberOfChannels);
  const sampleCount = audioBuffer.length;
  const mono = new Float32Array(sampleCount);
  for (let channel = 0; channel < channelCount; channel += 1) {
    const source = audioBuffer.getChannelData(channel);
    for (let i = 0; i < sampleCount; i += 1) {
      mono[i] += source[i] / channelCount;
    }
  }
  return mono;
};

const encodeMonoWav16 = (samples: Float32Array, sampleRate: number) => {
  const bytesPerSample = 2;
  const blockAlign = bytesPerSample;
  const dataSize = samples.length * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  const writeString = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) {
      view.setUint8(offset + i, text.charCodeAt(i));
    }
  };

  writeString(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * blockAlign, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, dataSize, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    const value = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
    view.setInt16(offset, Math.round(value), true);
    offset += 2;
  }
  return buffer;
};

const convertBlobToWavIfPossible = async (source: Blob) => {
  const sourceType = source.type.toLowerCase();
  if (sourceType.includes("wav")) return source;
  if (typeof window === "undefined" || typeof window.AudioContext === "undefined") {
    return source;
  }

  const audioContext = new window.AudioContext();
  try {
    const sourceBuffer = await source.arrayBuffer();
    const decoded = await audioContext.decodeAudioData(sourceBuffer.slice(0));
    const mono = downmixToMono(decoded);
    const wavBuffer = encodeMonoWav16(mono, decoded.sampleRate);
    return new Blob([wavBuffer], { type: "audio/wav" });
  } catch (error) {
    console.error("Session audio WAV conversion failed", error);
    return source;
  } finally {
    try {
      await audioContext.close();
    } catch {
      // noop
    }
  }
};

const stopSessionRecorderStream = () => {
  if (!sessionRecorderStream) return;
  sessionRecorderStream.getTracks().forEach((track) => track.stop());
  sessionRecorderStream = null;
};

const startSessionAudioRecorder = async () => {
  if (typeof window === "undefined") return;
  if (typeof MediaRecorder === "undefined") return;
  if (!navigator.mediaDevices?.getUserMedia) return;
  if (sessionRecorder) return;

  try {
    sessionRecorderStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    sessionAudioChunks = [];
    const preferredMimeType = pickSessionAudioMimeType();
    const recorder = preferredMimeType
      ? new MediaRecorder(sessionRecorderStream, { mimeType: preferredMimeType })
      : new MediaRecorder(sessionRecorderStream);

    sessionAudioMimeType = recorder.mimeType || preferredMimeType || "audio/webm";
    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        sessionAudioChunks.push(event.data);
      }
    };
    recorder.start(SESSION_AUDIO_TIMESLICE_MS);
    sessionRecorder = recorder;
  } catch (error) {
    console.error("Session audio recorder start failed", error);
    sessionRecorder = null;
    sessionAudioChunks = [];
    sessionAudioMimeType = "";
    stopSessionRecorderStream();
  }
};

const ensureSessionAudioRecorder = () => {
  if (sessionRecorder) return;
  void startSessionAudioRecorder();
};

const stopSessionAudioRecorder = async () => {
  const fallbackExtension = extensionFromMimeType(sessionAudioMimeType || "audio/webm");
  if (!sessionRecorder) {
    stopSessionRecorderStream();
    return { blob: null as Blob | null, extension: fallbackExtension };
  }

  const recorder = sessionRecorder;
  return await new Promise<{ blob: Blob | null; extension: string }>((resolve) => {
    let settled = false;
    const finalize = () => {
      if (settled) return;
      settled = true;

      const mimeType = recorder.mimeType || sessionAudioMimeType || "audio/webm";
      const blob =
        sessionAudioChunks.length > 0
          ? new Blob(sessionAudioChunks, { type: mimeType })
          : null;
      const extension = extensionFromMimeType(mimeType);

      sessionRecorder = null;
      sessionAudioChunks = [];
      sessionAudioMimeType = "";
      stopSessionRecorderStream();

      resolve({ blob, extension });
    };

    recorder.onstop = () => finalize();
    recorder.onerror = () => finalize();

    try {
      if (recorder.state !== "inactive") {
        recorder.stop();
      } else {
        finalize();
      }
    } catch {
      finalize();
    }
  });
};

const getElapsedMilliseconds = () => {
  if (!sessionStartTime.value) return 0;
  return Date.now() - sessionStartTime.value.getTime();
};

const getElapsedSeconds = () => Math.floor(getElapsedMilliseconds() / 1000);

const hasTimedOut = () => getElapsedMilliseconds() >= SESSION_LIMIT_MS;

const isCurrentSession = (token: number) =>
  isSessionActive.value && token === sessionToken;

const clamp01 = (value: number) => Math.min(1, Math.max(0, value));

const setAwaitingInputState = () => {
  state.value = isSessionActive.value ? "listening" : "idle";
};

const clearSessionTimer = () => {
  if (sessionTimer === null) return;
  clearTimeout(sessionTimer);
  sessionTimer = null;
};

const clearSoftWrapTimer = () => {
  if (softWrapTimer === null) return;
  clearTimeout(softWrapTimer);
  softWrapTimer = null;
};

const settleSpeakingPromise = () => {
  activeUtterance = null;
  if (!resolveSpeaking) return;
  const resolve = resolveSpeaking;
  resolveSpeaking = null;
  resolve();
};

const stopSpeakingLevelMeter = (resetLevel = true) => {
  if (speakingLevelTimer !== null) {
    clearInterval(speakingLevelTimer);
    speakingLevelTimer = null;
  }
  if (resetLevel) {
    voiceLevel.value = 0;
    isVoiceActive.value = false;
  }
};

const startSpeakingLevelMeter = () => {
  stopSpeakingLevelMeter(false);
  speakingLevelPhase = 0;
  isVoiceActive.value = false;

  speakingLevelTimer = window.setInterval(() => {
    const hasSpeechSynthesis =
      typeof window !== "undefined" && "speechSynthesis" in window;
    const isSynthSpeaking =
      hasSpeechSynthesis &&
      window.speechSynthesis.speaking &&
      !window.speechSynthesis.paused;

    if (!isSynthSpeaking) {
      const decayed = voiceLevel.value * 0.82;
      voiceLevel.value = decayed < 0.008 ? 0 : decayed;
      isVoiceActive.value = voiceLevel.value > SPEAKING_ACTIVE_THRESHOLD;
      return;
    }

    speakingLevelPhase += 0.22;
    const pulseA = Math.max(0, Math.sin(speakingLevelPhase)) * 0.22;
    const pulseB = Math.max(0, Math.sin(speakingLevelPhase * 1.5 + 0.35)) * 0.12;
    const target = clamp01(0.05 + pulseA + pulseB);
    const next = voiceLevel.value * 0.74 + target * 0.26;
    voiceLevel.value = clamp01(next);
    isVoiceActive.value = next > SPEAKING_ACTIVE_THRESHOLD;
  }, SPEAKING_LEVEL_INTERVAL_MS);
};

const stopMicMeterLoop = () => {
  if (micMeterRaf === null) return;
  cancelAnimationFrame(micMeterRaf);
  micMeterRaf = null;
};

const stopMicMeter = (resetLevel = true) => {
  micMeterToken += 1;
  stopMicMeterLoop();
  if (resetLevel) {
    voiceLevel.value = 0;
    isVoiceActive.value = false;
  }
};

const releaseMicMeterResources = async () => {
  stopMicMeter(false);

  if (micSourceNode) {
    try {
      micSourceNode.disconnect();
    } catch {
      // noop
    }
    micSourceNode = null;
  }

  micAnalyser = null;
  micWaveData = null;

  if (micStream) {
    micStream.getTracks().forEach((track) => track.stop());
    micStream = null;
  }

  if (micAudioContext) {
    try {
      await micAudioContext.close();
    } catch {
      // noop
    }
    micAudioContext = null;
  }
};

const ensureMicMeter = async () => {
  if (typeof window === "undefined" || !navigator.mediaDevices?.getUserMedia) {
    return false;
  }

  if (!micAudioContext) {
    micAudioContext = new window.AudioContext();
  }

  if (micAudioContext.state === "suspended") {
    await micAudioContext.resume();
  }

  if (!micStream && sessionRecorderStream) {
    // Reuse active recorder stream first to avoid opening another microphone stream.
    micStream = sessionRecorderStream;
  }

  if (!micStream) {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
  }

  if (!micAnalyser) {
    micAnalyser = micAudioContext.createAnalyser();
    micAnalyser.fftSize = 512;
    micAnalyser.smoothingTimeConstant = 0.78;
    micWaveData = new Uint8Array(micAnalyser.fftSize);
  }

  if (!micSourceNode) {
    micSourceNode = micAudioContext.createMediaStreamSource(micStream);
    micSourceNode.connect(micAnalyser);
  }

  return true;
};

const startMicMeter = async (token: number) => {
  stopSpeakingLevelMeter(false);

  try {
    const ready = await ensureMicMeter();
    if (!ready || !micAnalyser || !micWaveData) return;
  } catch (error) {
    console.error("Mic meter init failed", error);
    return;
  }

  const localMeterToken = ++micMeterToken;
  stopMicMeterLoop();

  const sample = () => {
    if (
      !isCurrentSession(token) ||
      state.value !== "listening" ||
      localMeterToken !== micMeterToken ||
      !micAnalyser ||
      !micWaveData
    ) {
      stopMicMeterLoop();
      return;
    }

    micAnalyser.getByteTimeDomainData(micWaveData);

    let energy = 0;
    for (let i = 0; i < micWaveData.length; i += 1) {
      const normalized = (micWaveData[i] - 128) / 128;
      energy += normalized * normalized;
    }

    const rms = Math.sqrt(energy / micWaveData.length);
    const measuredRaw = clamp01(Math.pow(rms * MIC_LEVEL_SENSITIVITY, 0.78));
    const measured = measuredRaw > MIC_LEVEL_SILENCE_FLOOR ? measuredRaw : 0;
    const next = voiceLevel.value * 0.56 + measured * 0.44;
    voiceLevel.value = clamp01(next);
    isVoiceActive.value = next > MIC_ACTIVE_THRESHOLD;

    micMeterRaf = window.requestAnimationFrame(sample);
  };

  micMeterRaf = window.requestAnimationFrame(sample);
};

const cancelSpeaking = () => {
  stopSpeakingLevelMeter();
  if (typeof window !== "undefined" && "speechSynthesis" in window) {
    window.speechSynthesis.cancel();
  }
  settleSpeakingPromise();
};

const isRecognitionActive = () => recognition !== null;

const clearSilenceCommitTimer = () => {
  if (silenceCommitTimer === null) return;
  clearTimeout(silenceCommitTimer);
  silenceCommitTimer = null;
};

const clearRecognitionBuffers = () => {
  stableTranscriptBuffer = "";
  interimTranscriptBuffer = "";
  incompleteCommitRetryCount = 0;
};

const composeBufferedTranscript = () =>
  [stableTranscriptBuffer, interimTranscriptBuffer].filter(Boolean).join(" ").trim();

const isLikelyFillerUtterance = (text: string) => {
  const normalized = text.replace(/\s+/g, "").toLowerCase();
  if (!normalized) return true;
  const fillers = ["음", "어", "응", "아", "그", "저", "그러니까", "그니까", "음음"];
  return fillers.includes(normalized) || normalized.length <= 1;
};

const isLikelyIncompleteUtterance = (text: string) => {
  const cleaned = text.trim();
  if (!cleaned) return false;
  if (cleaned.endsWith("...") || /[,\-…(（]$/.test(cleaned)) return true;
  if (/(요|다|죠|네|까|나요|군요|습니다|이에요|예요|였어요|했어요)[.!?]?$/.test(cleaned)) {
    return false;
  }
  if (
    /(은|는|이|가|을|를|에|에서|와|과|도|만|부터|까지|처럼|인|인데|면|고|서|인지|는지|거나|며)$/.test(
      cleaned
    )
  ) {
    return true;
  }
  if (/(무슨|어떤|왜|언제|어디|누가|뭐|무엇)$/.test(cleaned)) return true;
  return cleaned.replace(/\s+/g, "").length <= 1;
};

const stopRecognition = () => {
  stopMicMeter();
  clearSilenceCommitTimer();
  if (!recognition) return;

  try {
    isRecognitionStopping = true;
    recognition.stop();
  } catch {
    // already stopped
  }

  recognition = null;
};

const appendUserMessage = (text: string) => {
  messages.value.push({
    id: generateId(),
    role: "user",
    content: text,
    timestamp: new Date(),
  });
};

const appendAssistantMessage = (text: string) => {
  messages.value.push({
    id: generateId(),
    role: "assistant",
    content: text,
    timestamp: new Date(),
  });
};

const normalizeConversationPhase = (
  value: unknown
): ConversationPhase | null => {
  const phase = String(value ?? "").trim().toLowerCase();
  if (phase === "opening") return "opening";
  if (phase === "warmup") return "warmup";
  if (phase === "dialog") return "dialog";
  if (phase === "closing") return "closing";
  return null;
};

const inferPhaseByTime = (elapsedSec: number): ConversationPhase => {
  if (elapsedSec >= SOFT_WRAP_START_SEC) return "closing";
  if (elapsedSec < WARMUP_END_SEC) return "warmup";
  return "dialog";
};

const shouldRequestClose = (elapsedSec: number) => {
  if (elapsedSec >= SESSION_LIMIT_SEC - HARD_WRAP_BUFFER_SEC) return true;
  return elapsedSec >= SOFT_WRAP_START_SEC;
};

const inferClosingReason = (elapsedSec: number) => {
  if (elapsedSec >= SESSION_LIMIT_SEC - HARD_WRAP_BUFFER_SEC) return "timeout_hard";
  if (elapsedSec >= SOFT_WRAP_START_SEC) return "timeout_soft";
  return undefined;
};

const buildMetaPayload = (
  source: string,
  requestClose: boolean,
  overrides: Partial<ChatMetaPayload> = {}
): ChatMetaPayload => {
  const elapsedSec = getElapsedSeconds();
  const inferredPhase = inferPhaseByTime(elapsedSec);

  let phase: ConversationPhase;
  if (source === "session_start") {
    phase = "opening";
  } else {
    phase = conversationPhase.value;
    if (phase === "opening") {
      phase = inferredPhase;
    } else if (inferredPhase === "closing") {
      phase = "closing";
    }
  }
  if (requestClose) {
    phase = "closing";
  }
  conversationPhase.value = phase;

  const closingReason =
    overrides.closing_reason ?? (requestClose ? inferClosingReason(elapsedSec) : undefined);

  const parsedUserId = Number(authStore.userId);
  const resolvedPatientId =
    Number.isFinite(parsedUserId) && parsedUserId > 0
      ? Math.trunc(parsedUserId)
      : undefined;

  return {
    session_id: sessionId.value ?? undefined,
    patient_id: resolvedPatientId,
    profile_id: getOrCreateProfileId(),
    session_mode: "live",
    conversation_mode: DEFAULT_CONVERSATION_MODE,
    turn_index: turnIndex.value,
    elapsed_sec: elapsedSec,
    remaining_sec: Math.max(0, SESSION_LIMIT_SEC - elapsedSec),
    target_sec: SESSION_LIMIT_SEC,
    hard_limit_sec: SESSION_LIMIT_SEC,
    should_wrap_up: elapsedSec >= SOFT_WRAP_START_SEC,
    source,
    conversation_phase: phase,
    request_close: requestClose,
    closing_reason: closingReason,
    ...overrides,
  };
};

const requestGracefulTimeoutClose = async (token: number) => {
  if (!isCurrentSession(token)) return;
  if (timeoutCloseInProgress) return;
  timeoutCloseInProgress = true;

  stopRecognition();
  state.value = "processing";

  try {
    const response = await sendChat(
      "",
      DEFAULT_MODEL_RESULT,
      dialogState.value,
      buildMetaPayload("timeout_close", true, {
        stt_event: "no_speech",
        closing_reason: "timeout_hard",
      })
    );

    if (!isCurrentSession(token)) return;

    if (response.session_id) {
      sessionId.value = response.session_id;
    }
    dialogState.value = response.state ?? dialogState.value;
    const backendPhase = normalizeConversationPhase(response.meta?.conversation_phase);
    if (backendPhase) {
      conversationPhase.value = backendPhase;
    }

    const reply = response.response?.trim() || TIMEOUT_CLOSING_FALLBACK;
    const spoke = await botSpeak(reply, token);
    if (!spoke) return;
  } catch (error) {
    console.error("Graceful timeout close failed", error);
  }

  if (!isCurrentSession(token)) return;
  await stopSession("target_reached");
};

const stopIfTimedOut = async (token: number) => {
  if (!isCurrentSession(token)) return true;
  if (!hasTimedOut()) return false;
  await requestGracefulTimeoutClose(token);
  return true;
};

const botSpeak = async (text: string, token: number) => {
  if (!isCurrentSession(token)) return false;

  stopMicMeter(false);
  startSpeakingLevelMeter();
  state.value = "speaking";
  currentResponse.value = text;
  appendAssistantMessage(text);

  if (typeof window === "undefined" || !("speechSynthesis" in window)) {
    await new Promise<void>((resolve) => {
      globalThis.setTimeout(resolve, Math.min(2500, 700 + text.length * 20));
    });
  } else {
    await new Promise<void>((resolve) => {
      const utterance = new SpeechSynthesisUtterance(text);
      activeUtterance = utterance;
      resolveSpeaking = resolve;
      utterance.lang = "ko-KR";
      utterance.rate = 0.95;

      const settle = () => {
        settleSpeakingPromise();
      };

      utterance.onend = settle;
      utterance.onerror = settle;
      window.speechSynthesis.speak(utterance);
    });
  }

  stopSpeakingLevelMeter();
  if (!isCurrentSession(token)) return false;
  currentResponse.value = "";
  state.value = "cooldown";
  await new Promise<void>((resolve) => {
    globalThis.setTimeout(resolve, POST_SPEAK_INPUT_GRACE_MS);
  });
  if (!isCurrentSession(token)) return false;
  setAwaitingInputState();
  return true;
};

const scheduleSoftWrapCue = (token: number) => {
  void token;
  clearSoftWrapTimer();
};

const scheduleRecognitionRetry = (
  token: number,
  delayMs = LISTEN_RETRY_DELAY_MS
) => {
  window.setTimeout(() => {
    if (!isCurrentSession(token)) return;
    if (state.value === "processing" || state.value === "speaking" || state.value === "cooldown") return;
    if (isRecognitionActive()) return;
    startLiveRecognition(token);
  }, delayMs);
};

const handleNoSpeechDetected = async (token: number) => {
  if (!isCurrentSession(token)) return;
  if (state.value === "processing" || state.value === "speaking" || state.value === "cooldown") return;

  if (noSpeechRetryCount < NO_SPEECH_GRACE_RETRIES) {
    noSpeechRetryCount += 1;
    scheduleRecognitionRetry(token, NO_SPEECH_RETRY_DELAY_MS);
    return;
  }

  noSpeechRetryCount = 0;
  if (await stopIfTimedOut(token)) return;

  const elapsedBefore = getElapsedSeconds();
  const requestClose = shouldRequestClose(elapsedBefore);
  state.value = "processing";

  try {
    const response = await sendChat(
      "",
      DEFAULT_MODEL_RESULT,
      dialogState.value,
      buildMetaPayload("live_stt_no_speech", requestClose, {
        stt_event: "no_speech",
      })
    );

    if (!isCurrentSession(token)) return;

    if (response.session_id) {
      sessionId.value = response.session_id;
    }
    dialogState.value = response.state ?? dialogState.value;
    const backendPhase = normalizeConversationPhase(response.meta?.conversation_phase);
    if (backendPhase) {
      conversationPhase.value = backendPhase;
    }
    const backendRequestClose = Boolean(response.meta?.request_close);

    const reply = response.response?.trim() || EMERGENCY_RESPONSE_FALLBACK;
    const spoke = await botSpeak(reply, token);
    if (!spoke) return;

    if (backendRequestClose || requestClose) {
      conversationPhase.value = "closing";
      await stopSession("target_reached");
      return;
    }

    if (await stopIfTimedOut(token)) return;
    scheduleRecognitionRetry(token);
  } catch (error) {
    console.error("No-speech follow-up request failed", error);
    if (!isCurrentSession(token)) return;

    const spoke = await botSpeak(EMERGENCY_RESPONSE_FALLBACK, token);
    if (!spoke) return;
    if (await stopIfTimedOut(token)) return;
    scheduleRecognitionRetry(token, NO_SPEECH_RETRY_DELAY_MS);
  }
};

const extractTranscriptFromResults = (results: SpeechRecognitionResultList) => {
  let stable = "";
  let interim = "";

  for (let i = 0; i < results.length; i += 1) {
    const segment = results[i][0]?.transcript?.trim();
    if (!segment) continue;
    if (results[i].isFinal) {
      stable += `${segment} `;
    } else {
      interim += `${segment} `;
    }
  }

  return {
    stable: stable.trim(),
    interim: interim.trim(),
  };
};

const startLiveRecognition = (token: number) => {
  if (!isCurrentSession(token)) return;
  if (hasTimedOut()) {
    void requestGracefulTimeoutClose(token);
    return;
  }

  if (isRecognitionActive()) return;
  if (typeof window === "undefined") return;

  const speechWindow = window as Window & {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };
  const SpeechRecognition =
    speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    console.error("SpeechRecognition is not supported in this browser.");
    void stopSession("manual_stop");
    return;
  }

  stopSpeakingLevelMeter(false);
  clearSilenceCommitTimer();
  clearRecognitionBuffers();
  state.value = "listening";
  currentTranscript.value = "";
  void startMicMeter(token);

  const localRecognition = new SpeechRecognition();
  recognition = localRecognition;
  isRecognitionStopping = false;
  localRecognition.lang = "ko-KR";
  localRecognition.continuous = true;
  localRecognition.interimResults = true;

  const scheduleSilenceCommit = (delayMs = SPEECH_SILENCE_COMMIT_MS) => {
    clearSilenceCommitTimer();
    silenceCommitTimer = window.setTimeout(() => {
      if (!isCurrentSession(token)) return;

      const combined = composeBufferedTranscript();
      if (!combined) return;

      if (isLikelyFillerUtterance(combined)) {
        clearRecognitionBuffers();
        currentTranscript.value = "";
        return;
      }

      if (
        isLikelyIncompleteUtterance(combined) &&
        incompleteCommitRetryCount < INCOMPLETE_SILENCE_RETRY_LIMIT
      ) {
        incompleteCommitRetryCount += 1;
        scheduleSilenceCommit(SPEECH_SILENCE_COMMIT_MS + INCOMPLETE_SILENCE_EXTRA_MS);
        return;
      }

      currentTranscript.value = combined;
      clearRecognitionBuffers();
      stopRecognition();
      void handleUserTurn(combined, token);
    }, delayMs);
  };

  localRecognition.onresult = (event) => {
    if (!isCurrentSession(token)) return;

    // Start full-session recording only after STT actually starts receiving audio.
    ensureSessionAudioRecorder();

    const next = extractTranscriptFromResults(event.results);
    stableTranscriptBuffer = next.stable;
    interimTranscriptBuffer = next.interim;

    const combined = composeBufferedTranscript();
    if (!combined) return;

    sttNoSpeechRetryCount = 0;
    noSpeechRetryCount = 0;
    incompleteCommitRetryCount = 0;
    currentTranscript.value = combined;
    scheduleSilenceCommit();
  };

  localRecognition.onerror = (event) => {
    if (recognition === localRecognition) {
      recognition = null;
    }
    clearSilenceCommitTimer();

    if (!isCurrentSession(token)) return;

    const errorCode = event.error ?? "unknown";
    console.error("STT error", errorCode, event);

    if (errorCode === "aborted") return;

    if (errorCode === "no-speech") {
      if (sttNoSpeechRetryCount < STT_NO_SPEECH_RETRY_LIMIT) {
        sttNoSpeechRetryCount += 1;
        setAwaitingInputState();
        scheduleRecognitionRetry(token, NO_SPEECH_RETRY_DELAY_MS);
        return;
      }

      sttNoSpeechRetryCount = 0;
      void handleNoSpeechDetected(token);
      return;
    }

    if (hasTimedOut()) {
      void requestGracefulTimeoutClose(token);
      return;
    }

    setAwaitingInputState();
    scheduleRecognitionRetry(token);
  };

  localRecognition.onend = () => {
    if (recognition === localRecognition) {
      recognition = null;
    }
    clearSilenceCommitTimer();

    if (isRecognitionStopping) {
      isRecognitionStopping = false;
      return;
    }

    if (!isCurrentSession(token)) return;
    if (hasTimedOut()) {
      void requestGracefulTimeoutClose(token);
      return;
    }
    if (state.value === "processing" || state.value === "speaking" || state.value === "cooldown") return;

    setAwaitingInputState();
    scheduleRecognitionRetry(token);
  };

  try {
    localRecognition.start();
  } catch (error) {
    console.error("Failed to start SpeechRecognition", error);
    if (!isCurrentSession(token)) return;
    scheduleRecognitionRetry(token, NO_SPEECH_RETRY_DELAY_MS);
  }
};

const handleUserTurn = async (transcript: string, token: number) => {
  if (!isCurrentSession(token)) return;
  if (await stopIfTimedOut(token)) return;

  noSpeechRetryCount = 0;
  sttNoSpeechRetryCount = 0;
  incompleteCommitRetryCount = 0;
  const elapsedBefore = getElapsedSeconds();
  const requestClose = shouldRequestClose(elapsedBefore);

  currentTranscript.value = transcript;
  appendUserMessage(transcript);
  turnIndex.value += 1;
  state.value = "processing";

  try {
    const response = await sendChat(
      transcript,
      DEFAULT_MODEL_RESULT,
      dialogState.value,
      buildMetaPayload("live_stt", requestClose, {
        stt_event: "speech",
      })
    );

    if (!isCurrentSession(token)) return;

    if (response.session_id) {
      sessionId.value = response.session_id;
    }
    dialogState.value = response.state ?? dialogState.value;
    const backendPhase = normalizeConversationPhase(response.meta?.conversation_phase);
    if (backendPhase) {
      conversationPhase.value = backendPhase;
    }
    const backendRequestClose = Boolean(response.meta?.request_close);

    const spoke = await botSpeak(response.response, token);
    if (!spoke) return;

    if (backendRequestClose || requestClose) {
      conversationPhase.value = "closing";
      await stopSession("target_reached");
      return;
    }

    if (await stopIfTimedOut(token)) return;
    startLiveRecognition(token);
  } catch (error) {
    console.error("Chat request failed", error);
    if (!isCurrentSession(token)) return;
    await stopSession("manual_stop");
  }
};

const getOpeningMessage = async (token: number) => {
  try {
    const response = await startChatSession(
      DEFAULT_MODEL_RESULT,
      buildMetaPayload("session_start", false, {
        session_event: "session_start",
      })
    );

    if (!isCurrentSession(token)) return EMERGENCY_RESPONSE_FALLBACK;

    if (response.session_id) {
      sessionId.value = response.session_id;
    }
    dialogState.value = response.state ?? dialogState.value;
    const backendPhase = normalizeConversationPhase(response.meta?.conversation_phase);
    if (backendPhase) {
      conversationPhase.value = backendPhase;
    }

    const opening = response.response?.trim();
    return opening || EMERGENCY_RESPONSE_FALLBACK;
  } catch (error) {
    console.error("Start chat request failed", error);
    return EMERGENCY_RESPONSE_FALLBACK;
  }
};

const startSessionTimer = (token: number) => {
  clearSessionTimer();
  sessionTimer = window.setTimeout(() => {
    if (!isCurrentSession(token)) return;
    void requestGracefulTimeoutClose(token);
  }, SESSION_LIMIT_MS);
};

const startSession = async () => {
  if (isSessionActive.value) return;

  sessionToken += 1;
  const token = sessionToken;

  state.value = "idle";
  isSessionActive.value = true;
  sessionStartTime.value = new Date();
  sessionId.value = generateSessionId();
  dialogState.value = null;
  turnIndex.value = 0;
  currentTranscript.value = "";
  currentResponse.value = "";
  messages.value = [];
  conversationPhase.value = "opening";
  noSpeechRetryCount = 0;
  sttNoSpeechRetryCount = 0;
  incompleteCommitRetryCount = 0;
  timeoutCloseInProgress = false;

  startSessionTimer(token);
  scheduleSoftWrapCue(token);

  const openingText = await getOpeningMessage(token);
  if (!isCurrentSession(token)) return;

  const spoke = await botSpeak(openingText, token);
  if (!spoke) return;
  if (await stopIfTimedOut(token)) return;

  startLiveRecognition(token);
};

const stopSession = async (reason: SessionEndReason, clearMessages = false) => {
  const endedSessionId = sessionId.value;
  const endedProfileId = getOrCreateProfileId();
  const elapsedSec = getElapsedSeconds();
  const completedTurns = turnIndex.value;
  const recordedAudio = await stopSessionAudioRecorder();
  let uploadBlob = recordedAudio.blob;
  let uploadExtension = recordedAudio.extension;

  if (uploadBlob) {
    const convertedBlob = await convertBlobToWavIfPossible(uploadBlob);
    if (convertedBlob.type.toLowerCase().includes("wav")) {
      uploadBlob = convertedBlob;
      uploadExtension = "wav";
    } else {
      uploadBlob = convertedBlob;
    }
  }

  sessionToken += 1;
  clearSessionTimer();
  clearSoftWrapTimer();
  stopRecognition();
  cancelSpeaking();
  await releaseMicMeterResources();

  isSessionActive.value = false;
  state.value = "idle";
  currentTranscript.value = "";
  currentResponse.value = "";
  sessionStartTime.value = null;
  sessionId.value = null;
  dialogState.value = null;
  turnIndex.value = 0;
  conversationPhase.value = "opening";
  noSpeechRetryCount = 0;
  sttNoSpeechRetryCount = 0;
  incompleteCommitRetryCount = 0;
  timeoutCloseInProgress = false;

  if (clearMessages) {
    messages.value = [];
  }

  if (endedSessionId) {
    if (uploadBlob) {
      try {
        const mimeType = uploadBlob.type || `audio/${uploadExtension}`;
        const uploadFile = new File(
          [uploadBlob],
          `conversation.user.${uploadExtension}`,
          { type: mimeType }
        );
        await uploadSessionAudio(endedSessionId, uploadFile, endedProfileId);
      } catch (error) {
        console.error("Session audio upload failed", error);
      }
    }

    try {
      await endChatSession({
        session_id: endedSessionId,
        end_reason: reason,
        elapsed_sec: elapsedSec,
        turn_count: completedTurns,
        session_mode: "live",
      });
    } catch (error) {
      console.error("Session end request failed", error);
    }
  }
};

const resetSession = () => {
  if (!isSessionActive.value) {
    clearSessionTimer();
    clearSoftWrapTimer();
    stopRecognition();
    cancelSpeaking();
    void stopSessionAudioRecorder();
    void releaseMicMeterResources();
    state.value = "idle";
    currentTranscript.value = "";
    currentResponse.value = "";
    sessionStartTime.value = null;
    sessionId.value = null;
    dialogState.value = null;
    turnIndex.value = 0;
    conversationPhase.value = "opening";
    noSpeechRetryCount = 0;
    sttNoSpeechRetryCount = 0;
    incompleteCommitRetryCount = 0;
    timeoutCloseInProgress = false;
    messages.value = [];
    return;
  }

  void stopSession("reset", true);
};

export function useVoiceSession() {
  const isListening = computed(() => state.value === "listening");
  const isProcessing = computed(() => state.value === "processing");
  const isSpeaking = computed(() => state.value === "speaking");
  const isMockMode = computed(() => false);

  const startListening = () => {
    if (!isSessionActive.value) {
      void startSession();
      return;
    }

    if (state.value !== "idle") return;
    if (hasTimedOut()) {
      void requestGracefulTimeoutClose(sessionToken);
      return;
    }

    startLiveRecognition(sessionToken);
  };

  const stopListening = () => {
    if (!isSessionActive.value) return;
    void stopSession("manual_stop");
  };

  return {
    state: readonly(state),
    messages: readonly(messages),
    currentTranscript: readonly(currentTranscript),
    currentResponse: readonly(currentResponse),
    voiceLevel: readonly(voiceLevel),
    isVoiceActive: readonly(isVoiceActive),
    sessionStartTime: readonly(sessionStartTime),
    isSessionActive: readonly(isSessionActive),
    isListening,
    isProcessing,
    isSpeaking,
    isMockMode,
    startListening,
    stopListening,
    resetSession,
  };
}
