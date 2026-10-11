/**
 * Ring for a waiting web chat: a short two-tone WebAudio beep plus, when the
 * browser permission is already granted, a desktop notification.
 *
 * Rings only while the agent is available and the chat's topic is one they
 * take (an empty topic list means "all topics"), and only once per session.
 * Browsers block audio before a user gesture, so the AudioContext is created
 * lazily on the first pointer/key event; before that a ring is silent.
 */
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import i18n from "@/i18n";
import {
  chatAgentStateKey,
  chatInboxKey,
  chatTopicsKey,
  webchatApi,
} from "./webchatApi";

export type RingContext = {
  available: boolean;
  /** Topic refs the agent takes; empty = every topic. */
  topics: string[];
  /** Optional ref → label for the notification body. */
  labels?: Record<string, string>;
};

const TONE_HZ = 880;
const TONE_MS = 120;
const TONE_GAP_MS = 80;
const MAX_REMEMBERED_SESSIONS = 200;

let context: RingContext = { available: false, topics: [] };
const rung = new Set<string>();
let audio: AudioContext | null = null;
let unlocked = false;
let gestureListening = false;

export function setRingContext(ctx: RingContext): void {
  context = ctx;
}

function ensureAudio(): AudioContext | null {
  if (!unlocked) return null;
  if (audio) return audio;
  const Ctor: typeof AudioContext | undefined =
    typeof window === "undefined"
      ? undefined
      : (window.AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext);
  if (!Ctor) return null;
  try {
    audio = new Ctor();
  } catch {
    audio = null;
  }
  return audio;
}

function onGesture(): void {
  unlocked = true;
  const ctx = ensureAudio();
  if (ctx && ctx.state === "suspended") void ctx.resume().catch(() => undefined);
}

function listenForGesture(): void {
  if (gestureListening || typeof window === "undefined") return;
  gestureListening = true;
  for (const evt of ["pointerdown", "keydown"] as const) {
    window.addEventListener(evt, onGesture, { once: true, capture: true });
  }
}

/** Two 880 Hz tones of 120 ms. Silent (no throw) while audio is still locked. */
function beep(): void {
  const ctx = ensureAudio();
  if (!ctx) return;
  try {
    if (ctx.state === "suspended") void ctx.resume().catch(() => undefined);
    const start = ctx.currentTime;
    for (let i = 0; i < 2; i += 1) {
      const at = start + (i * (TONE_MS + TONE_GAP_MS)) / 1000;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = TONE_HZ;
      gain.gain.value = 0.15;
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(at);
      osc.stop(at + TONE_MS / 1000);
    }
  } catch {
    // Audio is a nicety; never let it break event handling.
  }
}

function notify(sessionId: string, topicRef: string | null | undefined): void {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  try {
    const body = (topicRef && context.labels?.[topicRef]) || "";
    new Notification(i18n.t("webchat.agent.ringTitle"), { body, tag: sessionId });
  } catch {
    // Some platforms only allow notifications from a service worker.
  }
}

/** A chat started waiting (SSE `chat_waiting`). */
export function receiveChatWaiting(msg: { session_id: string; topic_ref?: string | null }): void {
  listenForGesture();
  if (!context.available) return;
  if (context.topics.length > 0 && !(msg.topic_ref && context.topics.includes(msg.topic_ref))) {
    return;
  }
  if (rung.has(msg.session_id)) return;
  rung.add(msg.session_id);
  if (rung.size > MAX_REMEMBERED_SESSIONS) {
    const oldest = rung.values().next().value;
    if (oldest !== undefined) rung.delete(oldest);
  }
  beep();
  notify(msg.session_id, msg.topic_ref);
}

/** Test hook: forget rung sessions and audio state. */
export function resetChatRing(): void {
  context = { available: false, topics: [] };
  rung.clear();
  audio = null;
  unlocked = false;
  gestureListening = false;
}

/**
 * Keeps the ring context in sync with the agent's availability and topics and
 * returns how many chats are waiting for pickup. Mount once in the agent shell.
 */
export function useChatRing(): { pending: number } {
  const topics = useQuery({
    queryKey: chatTopicsKey,
    queryFn: ({ signal }) => webchatApi.topics(signal),
  });
  // Agents without any chat topic only ever cost the topics request.
  const isChatAgent = (topics.data?.length ?? 0) > 0;
  const state = useQuery({
    queryKey: chatAgentStateKey,
    enabled: isChatAgent,
    queryFn: ({ signal }) => webchatApi.getAgentState(signal),
  });
  const inbox = useQuery({
    queryKey: chatInboxKey,
    enabled: isChatAgent,
    queryFn: ({ signal }) => webchatApi.inbox(signal),
  });

  useEffect(() => {
    listenForGesture();
  }, []);

  useEffect(() => {
    const labels: Record<string, string> = {};
    for (const t of topics.data ?? []) labels[t.ref] = t.label;
    setRingContext({
      available: state.data?.available ?? false,
      topics: state.data?.topics ?? [],
      labels,
    });
  }, [state.data, topics.data]);

  return { pending: inbox.data?.waiting.length ?? 0 };
}
