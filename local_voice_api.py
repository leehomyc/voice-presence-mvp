#!/usr/bin/env python3
"""Small local HTTP API for speaking arbitrary text with ElevenLabs."""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
from collections import deque
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv

from speak_to_zoom import (
    DEFAULT_MODEL_ID,
    DEFAULT_OUTPUT_FORMAT,
    Config,
    elevenlabs_pcm_stream,
    fetch_voices,
    get_audio_devices,
    get_output_channels,
    play_pcm_stream,
    resolve_output_device,
    sample_rate_from_output_format,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
load_dotenv()
WORKSPACE_ROOT = os.getenv("VOICE_WORKSPACE_ROOT", os.getcwd())
STYLE_PATH = os.getenv("VOICE_STYLE_FILE", os.path.join(WORKSPACE_ROOT, "voice-style.md"))
MEMORY_PATH = os.getenv("VOICE_MEMORY_FILE", os.path.join(WORKSPACE_ROOT, "voice-memory.md"))
CONTEXT_PACK_PATH = os.path.join(os.path.dirname(__file__), "context", "current_context_pack.md")
TRANSCRIPT_DIR = os.path.join(os.path.dirname(__file__), "transcripts")
DEFAULT_VOICE = os.getenv("ELEVENLABS_VOICE") or os.getenv("ELEVENLABS_VOICE_ID") or ""
PERSONA_NAME = os.getenv("VOICE_PERSONA_NAME", "the user").strip() or "the user"
SPEAK_LOCK = threading.Lock()
MEETING_CONTEXT: deque[str] = deque(maxlen=12)
TRANSCRIPT_LINES: deque[dict[str, str]] = deque(maxlen=500)


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Local Voice API</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; max-width: 840px; }
    textarea { box-sizing: border-box; width: 100%; min-height: 140px; font: inherit; padding: 12px; }
    input, button, select { font: inherit; padding: 8px 10px; margin: 4px 4px 4px 0; }
    label { display: block; margin-top: 14px; font-weight: 600; }
    .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    #status { margin-top: 16px; white-space: pre-wrap; }
  </style>
</head>
<body>
  <h1>Local Voice API</h1>
  <label for="text">Text</label>
  <textarea id="text" placeholder="Type anything to speak..."></textarea>
  <label for="meeting">Meeting context</label>
  <textarea id="meeting" placeholder="Paste transcript notes or what people just said..."></textarea>
  <div class="row">
    <label>Voice <input id="voice" placeholder="ElevenLabs voice ID or name"></label>
    <label>Device <input id="device" value="BlackHole 2ch"></label>
    <label>Model <input id="model" value="eleven_flash_v2_5"></label>
  </div>
  <button id="speak">Speak</button>
  <button id="addContext">Add context</button>
  <button id="addTranscript">Add transcript</button>
  <button id="draft">Draft reply</button>
  <button id="liveStart">Start live</button>
  <button id="liveStop" disabled>Stop live</button>
  <button id="notion">Notion transcript</button>
  <button id="health">Check setup</button>
  <button id="devices">Devices</button>
  <button id="voices">Voices</button>
  <pre id="status"></pre>
  <script>
    const $ = (id) => document.getElementById(id);
    let recognition = null;
    let liveRunning = false;
    let liveBusy = false;
    const liveQueue = [];
    const show = (value) => $("status").textContent =
      typeof value === "string" ? value : JSON.stringify(value, null, 2);
    async function loadConfig() {
      try {
        const health = await (await fetch("/health")).json();
        const config = health.config || {};
        if (config.voice) $("voice").value = config.voice;
        if (config.output_device) $("device").value = config.output_device;
        if (config.model_id) $("model").value = config.model_id;
      } catch (_) {}
    }

    async function sendLiveUtterance(text) {
      const clean = text.trim();
      if (!clean) return;
      await fetch("/transcript", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: clean, speaker: "remote" })
      });
      const res = await fetch("/draft-reply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          instruction: $("text").value || "Reply naturally and briefly for a live Zoom conversation.",
          context: clean,
          speaker: "remote",
          speak: true,
          voice: $("voice").value,
          device: $("device").value,
          model_id: $("model").value
        })
      });
      return await res.json();
    }

    async function drainLiveQueue() {
      if (liveBusy || liveQueue.length === 0) return;
      liveBusy = true;
      const text = liveQueue.shift();
      show("Heard: " + text + "\\nDrafting and speaking...");
      try {
        const result = await sendLiveUtterance(text);
        show(result);
      } catch (err) {
        show("Live error: " + err);
      } finally {
        liveBusy = false;
        if (liveQueue.length) drainLiveQueue();
      }
    }

    $("speak").onclick = async () => {
      show("Speaking...");
      const res = await fetch("/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: $("text").value,
          voice: $("voice").value,
          device: $("device").value,
          model_id: $("model").value
        })
      });
      show(await res.json());
    };
    $("addContext").onclick = async () => {
      const res = await fetch("/context", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: $("meeting").value })
      });
      show(await res.json());
    };
    $("addTranscript").onclick = async () => {
      const res = await fetch("/transcript", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: $("meeting").value, speaker: "meeting" })
      });
      show(await res.json());
    };
    $("draft").onclick = async () => {
      show("Drafting...");
      const res = await fetch("/draft-reply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          instruction: $("text").value,
          context: $("meeting").value,
          speak: true,
          voice: $("voice").value,
          device: $("device").value,
          model_id: $("model").value
        })
      });
      show(await res.json());
    };
    $("liveStart").onclick = async () => {
      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SpeechRecognition) {
        show("This browser does not expose SpeechRecognition. Use Chrome, or paste text and use Draft reply.");
        return;
      }
      recognition = new SpeechRecognition();
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.lang = navigator.language || "en-US";
      liveRunning = true;
      $("liveStart").disabled = true;
      $("liveStop").disabled = false;
      recognition.onresult = (event) => {
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i];
          if (result.isFinal) {
            const text = result[0].transcript.trim();
            if (text) {
              liveQueue.push(text);
              drainLiveQueue();
            }
          }
        }
      };
      recognition.onerror = (event) => show("Speech recognition error: " + event.error);
      recognition.onend = () => {
        if (liveRunning) {
          try { recognition.start(); } catch (_) {}
        }
      };
      recognition.start();
      show("Live listening...");
    };
    $("liveStop").onclick = () => {
      liveRunning = false;
      $("liveStart").disabled = false;
      $("liveStop").disabled = true;
      if (recognition) recognition.stop();
      show("Live stopped.");
    };
    $("health").onclick = async () => show(await (await fetch("/health")).json());
    $("devices").onclick = async () => show(await (await fetch("/devices")).json());
    $("voices").onclick = async () => show(await (await fetch("/voices?query=" + encodeURIComponent($("voice").value))).json());
    $("notion").onclick = async () => show(await (await fetch("/notion-transcript")).json());
    loadConfig();
  </script>
</body>
</html>
"""


def public_config() -> dict[str, Any]:
    return {
        "has_api_key": bool(os.getenv("ELEVENLABS_API_KEY", "").strip()),
        "has_openai_key": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "voice": os.getenv("ELEVENLABS_VOICE") or os.getenv("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE,
        "model_id": os.getenv("ELEVENLABS_MODEL_ID", DEFAULT_MODEL_ID),
        "output_device": os.getenv("ELEVENLABS_OUTPUT_DEVICE", "BlackHole 2ch"),
        "output_format": os.getenv("ELEVENLABS_OUTPUT_FORMAT", DEFAULT_OUTPUT_FORMAT),
        "style_file": STYLE_PATH,
        "memory_file": MEMORY_PATH,
        "context_pack_file": CONTEXT_PACK_PATH,
        "notion_transcript_page_id": os.getenv("NOTION_TRANSCRIPT_PAGE_ID", ""),
        "notion_transcript_page_url": os.getenv("NOTION_TRANSCRIPT_PAGE_URL", ""),
    }


def make_config(payload: dict[str, Any]) -> Config:
    api_key = str(payload.get("api_key") or os.getenv("ELEVENLABS_API_KEY", "")).strip()
    voice = str(
        payload.get("voice")
        or payload.get("voice_id")
        or os.getenv("ELEVENLABS_VOICE")
        or os.getenv("ELEVENLABS_VOICE_ID")
        or DEFAULT_VOICE
    ).strip()
    model_id = str(payload.get("model_id") or os.getenv("ELEVENLABS_MODEL_ID", DEFAULT_MODEL_ID)).strip()
    device = payload.get("device") or os.getenv("ELEVENLABS_OUTPUT_DEVICE", "BlackHole 2ch")
    device_name = str(device).strip() if device is not None else None
    if not api_key:
        raise ValueError("Missing ELEVENLABS_API_KEY in .env.")
    if not voice:
        raise ValueError("Missing voice. Set ELEVENLABS_VOICE in .env or pass voice in JSON.")
    return Config(api_key=api_key, voice=voice, model_id=model_id, output_device=device_name)


def speak(payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("text", "")).strip()
    if not text:
        raise ValueError("Missing text.")
    config = make_config(payload)
    output_format = str(payload.get("output_format") or os.getenv("ELEVENLABS_OUTPUT_FORMAT", DEFAULT_OUTPUT_FORMAT))
    sample_rate = sample_rate_from_output_format(output_format)
    device_index = resolve_output_device(config.output_device)
    channels = get_output_channels(device_index, int(payload.get("channels", 2)))
    chunks = elevenlabs_pcm_stream(
        config=config,
        text=text,
        output_format=output_format,
        stability=float(payload.get("stability", 0.35)),
        similarity_boost=float(payload.get("similarity_boost", 0.9)),
        speed=float(payload.get("speed", 1.0)),
    )
    with SPEAK_LOCK:
        play_pcm_stream(chunks, sample_rate, device_index, channels, None, float(payload.get("gain", 1.0)))
    return {
        "ok": True,
        "spoken_chars": len(text),
        "voice": config.voice,
        "model_id": config.model_id,
        "device": config.output_device,
        "output_format": output_format,
    }


def read_excerpt(path: str, max_chars: int) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read(max_chars)
    except FileNotFoundError:
        return ""


def read_tail(path: str, max_chars: int) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
            return text[-max_chars:]
    except FileNotFoundError:
        return ""


def read_limited(path: str, max_chars: int) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read(max_chars + 1)
            if len(text) > max_chars:
                return text[:max_chars] + "\n[TRUNCATED: increase LIVE_MEMORY_CONTEXT_CHARS to include more.]"
            return text
    except FileNotFoundError:
        return ""


def int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def context_terms(text: str) -> set[str]:
    lowered = text.lower()
    terms = set(re.findall(r"[a-z0-9_@./:-]{2,}", lowered))
    cjk_runs = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    for run in cjk_runs:
        terms.update(run[i : i + 2] for i in range(max(0, len(run) - 1)))
        terms.update(run[i : i + 3] for i in range(max(0, len(run) - 2)))
    return terms


def relevant_excerpt(path: str, query: str, max_chars: int) -> str:
    terms = context_terms(query)
    if not terms:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except FileNotFoundError:
        return ""

    scored: list[tuple[float, int, str]] = []
    total = max(1, len(lines))
    for index, raw in enumerate(lines):
        line = raw.strip()
        if len(line) < 8:
            continue
        line_terms = context_terms(line)
        overlap = len(terms & line_terms)
        if overlap <= 0:
            continue
        recency_boost = index / total * 0.35
        scored.append((overlap + recency_boost, index, line))

    selected = sorted(scored, reverse=True)[:36]
    selected = sorted(selected, key=lambda item: item[1])
    output: list[str] = []
    used = 0
    for _score, _index, line in selected:
        add = line[:500]
        if used + len(add) + 1 > max_chars:
            break
        output.append(add)
        used += len(add) + 1
    return "\n".join(output)


def expanded_retrieval_query(query: str) -> str:
    lowered = query.lower()
    extras: list[str] = []
    if any(token in query for token in ("在哪", "哪里", "哪裡", "城市", "位置")) or "where" in lowered or "location" in lowered:
        extras.append("current location current city travel")
    if any(token in query for token in ("最近", "忙", "研究", "论文", "論文", "学校", "學校", "会议", "會議", "会", "會")):
        extras.append("current active recent research school tasks meetings AI product work personal context")
    if any(token in query for token in ("谁", "誰", "主任", "学校", "學校", "university", "school")):
        extras.append("school university department research")
    if any(
        token in query
        for token in ("私事", "工作之外", "工作之余", "平常", "兴趣", "興趣", "爱好", "愛好", "对象", "對象", "结婚", "結婚", "多大", "哪里人", "哪裡人")
    ):
        extras.append("personal soft context direct chats casual style leisure social eating friends travel food events games")
    return "\n".join([query, *extras])


def is_location_query(query: str) -> bool:
    lowered = query.lower()
    return any(token in query for token in ("在哪", "哪里", "哪裡", "城市", "位置", "纽约", "紐約")) or any(
        token in lowered for token in ("where", "location", "city", "nyc", "new york")
    )


def session_facts(query: str = "") -> str:
    location = os.getenv("LIVE_CURRENT_LOCATION", "").strip()
    context = os.getenv(
        "LIVE_CURRENT_CONTEXT",
        "Live voice-agent MVP test with ElevenLabs, OpenAI, and virtual audio routing.",
    ).strip()
    work_context = os.getenv(
        "LIVE_WORK_CONTEXT",
        "",
    ).strip()
    facts = [
        f"Persona name: {PERSONA_NAME}.",
        f"Current session context, only when relevant: {context}",
        f"Recent work context, only when asked about work/research/recent activity: {work_context}",
        "Do not mention location unless the other person asks about location, travel, or it is clearly relevant.",
        "Do not invent private biographical facts such as relationship status, age, family details, or exact travel dates. If not clearly in memory/context, answer lightly or decline.",
    ]
    if is_location_query(query) and location:
        facts.insert(1, f"Current location, because the current question asks about location: {location}.")
    return "\n".join(facts)


def add_context(text: str) -> dict[str, Any]:
    clean = " ".join(text.strip().split())
    if not clean:
        raise ValueError("Missing context text.")
    MEETING_CONTEXT.append(clean[-1200:])
    return {"ok": True, "context_items": len(MEETING_CONTEXT)}


def transcript_path() -> str:
    os.makedirs(TRANSCRIPT_DIR, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(TRANSCRIPT_DIR, f"meeting-transcript-{date}.md")


def add_transcript(text: str, speaker: str = "meeting") -> dict[str, Any]:
    clean = " ".join(text.strip().split())
    if not clean:
        raise ValueError("Missing transcript text.")
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    item = {"time": timestamp, "speaker": speaker.strip() or "meeting", "text": clean}
    TRANSCRIPT_LINES.append(item)
    path = transcript_path()
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"- {timestamp} **{item['speaker']}**: {clean}\n")
    add_context(clean)
    return {"ok": True, "transcript_items": len(TRANSCRIPT_LINES), "path": path}


def notion_transcript_status() -> dict[str, Any]:
    return {
        "ok": True,
        "page_id": os.getenv("NOTION_TRANSCRIPT_PAGE_ID", ""),
        "page_url": os.getenv("NOTION_TRANSCRIPT_PAGE_URL", ""),
        "local_transcript_path": transcript_path(),
        "transcript_items": len(TRANSCRIPT_LINES),
        "note": "Top-level Notion page is configured. Automatic background append needs a Notion API token or Codex/Notion connector run.",
    }


def draft_reply(payload: dict[str, Any]) -> dict[str, Any]:
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not openai_api_key:
        return {
            "ok": False,
            "error": "Missing OPENAI_API_KEY in .env. Add it locally before generating live replies.",
            "context_items": len(MEETING_CONTEXT),
            "style_file": STYLE_PATH,
            "memory_file": MEMORY_PATH,
        }

    instruction = str(payload.get("instruction") or payload.get("text") or "").strip()
    live_context = str(payload.get("context") or "").strip()
    speaker = str(payload.get("speaker") or "meeting").strip()
    latest_text = str(payload.get("text") or live_context or instruction).strip()
    recent_context = "\n".join(MEETING_CONTEXT)
    recent_transcript = "\n".join(
        f"{item['time']} {item['speaker']}: {item['text']}" for item in list(TRANSCRIPT_LINES)[-20:]
    )
    retrieval_query = expanded_retrieval_query(
        "\n".join([instruction, live_context, latest_text, recent_context, recent_transcript])
    )
    style_excerpt = "\n".join(
        part for part in [
            relevant_excerpt(STYLE_PATH, retrieval_query, int_env("LIVE_STYLE_RELEVANT_CHARS", 9000)),
            read_tail(STYLE_PATH, int_env("LIVE_STYLE_RECENT_CHARS", 6000)),
        ] if part.strip()
    )
    memory_mode = str(payload.get("memory_mode") or os.getenv("LIVE_MEMORY_MODE", "large")).strip().lower()
    if memory_mode == "full":
        memory_excerpt = "\n".join(
            part for part in [
                read_limited(STYLE_PATH, int_env("LIVE_FULL_STYLE_CHARS", 120000)),
                read_limited(MEMORY_PATH, int_env("LIVE_FULL_MEMORY_CHARS", 900000)),
            ] if part.strip()
        )
    else:
        memory_excerpt = "\n".join(
            part for part in [
                relevant_excerpt(MEMORY_PATH, retrieval_query, int_env("LIVE_MEMORY_RELEVANT_CHARS", 50000)),
                read_tail(MEMORY_PATH, int_env("LIVE_MEMORY_RECENT_CHARS", 25000)),
            ] if part.strip()
        )
    context_pack_excerpt = "\n".join(
        part for part in [
            relevant_excerpt(CONTEXT_PACK_PATH, retrieval_query, 4500),
            read_tail(CONTEXT_PACK_PATH, 3500),
        ] if part.strip()
    )

    prompt = f"""
You are drafting what {PERSONA_NAME} should say aloud in a live meeting.
Keep it natural and speakable. For simple acknowledgements, 1 sentence is fine. When asked for details, use 2-4 concise sentences and be more concrete.
If Chinese is appropriate, use casual Chinese. If English is appropriate, use concise English.
Do not introduce yourself as an AI in every reply, but be honest if asked. Do not over-explain.
Use the provided memory/context when it is relevant. If the memory does not clearly apply, be honest and brief instead of inventing details.
Prefer current meeting context and the current context pack over older long-term memory for current facts.
Answer the latest utterance directly before adding context. Do not answer a nearby older question unless the latest utterance depends on it.
If the latest utterance corrects you with words like "不是", "我是问", "工作之外", or "具体一点", treat that correction as the main task and stop repeating the earlier topic.
For personal/casual questions, privacy is okay in this local prototype: answer naturally from memory/context when available. If the fact is not known, say it casually and do not pivot back to work.
Never answer a personal question by saying "if you want to talk about work/projects..." unless the other person asks for work.
If asked about hobbies or life outside work, do not list teaching, admin, reviews, or student projects unless those are actually relevant.
Current local time: {datetime.now().astimezone().isoformat(timespec="minutes")}
Use the high-priority facts as constraints, not as topics to bring up unprompted.
Avoid repeating the same fact across consecutive replies unless asked again.

High-priority facts and constraints:
{session_facts(retrieval_query)}

Style notes:
{style_excerpt}

Current context pack from calendar/email/Notion/manual notes:
{context_pack_excerpt}

Memory/context notes:
{memory_excerpt}

Recent meeting context:
{recent_context}

Recent local transcript:
{recent_transcript}

Current speaker: {speaker}
What they just said / current context:
{latest_text}

Before answering, silently identify the exact question in the latest utterance and answer that question first.

User instruction:
{instruction or "Reply naturally and briefly."}
""".strip()

    model = str(payload.get("openai_model") or os.getenv("OPENAI_MODEL", "gpt-4o-mini")).strip()
    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {openai_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "input": prompt,
            "max_output_tokens": int(payload.get("max_output_tokens", 240)),
        },
        timeout=45,
    )
    if response.status_code >= 400:
        return {
            "ok": False,
            "error": f"OpenAI request failed with HTTP {response.status_code}.",
            "details": response.text[:1000],
            "model": model,
        }
    data = response.json()
    reply = str(data.get("output_text") or "").strip()
    if not reply:
        parts: list[str] = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"}:
                    parts.append(str(content.get("text", "")))
        reply = "".join(parts).strip()
    if not reply:
        return {"ok": False, "error": "OpenAI returned no text.", "model": model}

    result: dict[str, Any] = {
        "ok": True,
        "reply": reply,
        "model": model,
        "memory_mode": memory_mode,
        "prompt_chars": len(prompt),
    }
    if bool(payload.get("speak", False)):
        speech_payload = dict(payload)
        speech_payload["text"] = reply
        result["speech"] = speak(speech_payload)
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalVoiceAPI/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, status: int, data: dict[str, Any] | list[Any]) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = INDEX_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/health":
            devices = get_audio_devices()
            virtual_outputs = [
                d for d in devices
                if d["output_channels"] > 0 and any(h in d["name"].lower() for h in ("blackhole", "loopback", "cable", "vb-audio"))
            ]
            self.send_json(HTTPStatus.OK, {"ok": True, "config": public_config(), "virtual_outputs": virtual_outputs})
            return
        if parsed.path == "/context":
            self.send_json(HTTPStatus.OK, {"context": list(MEETING_CONTEXT)})
            return
        if parsed.path == "/transcript":
            self.send_json(HTTPStatus.OK, {"transcript": list(TRANSCRIPT_LINES), "path": transcript_path()})
            return
        if parsed.path == "/notion-transcript":
            self.send_json(HTTPStatus.OK, notion_transcript_status())
            return
        if parsed.path == "/context-pack":
            self.send_json(
                HTTPStatus.OK,
                {
                    "path": CONTEXT_PACK_PATH,
                    "text": read_tail(CONTEXT_PACK_PATH, 12000),
                },
            )
            return
        if parsed.path == "/devices":
            self.send_json(HTTPStatus.OK, {"devices": get_audio_devices()})
            return
        if parsed.path == "/voices":
            api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
            if not api_key:
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Missing ELEVENLABS_API_KEY in .env."})
                return
            query = parse_qs(parsed.query).get("query", [""])[0].lower()
            voices = fetch_voices(api_key)
            slim = [
                {
                    "voice_id": voice.get("voice_id"),
                    "name": voice.get("name"),
                    "category": voice.get("category"),
                }
                for voice in voices
                if not query
                or query in str(voice.get("name", "")).lower()
                or query in str(voice.get("voice_id", "")).lower()
            ]
            self.send_json(HTTPStatus.OK, {"voices": slim})
            return
        self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found."})

    def do_POST(self) -> None:
        try:
            payload = self.read_json()
            if self.path == "/speak":
                self.send_json(HTTPStatus.OK, speak(payload))
                return
            if self.path == "/context":
                self.send_json(HTTPStatus.OK, add_context(str(payload.get("text", ""))))
                return
            if self.path == "/transcript":
                self.send_json(
                    HTTPStatus.OK,
                    add_transcript(str(payload.get("text", "")), str(payload.get("speaker", "meeting"))),
                )
                return
            if self.path == "/draft-reply":
                self.send_json(HTTPStatus.OK, draft_reply(payload))
                return
            self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found."})
        except (Exception, SystemExit) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a local ElevenLabs voice API.")
    parser.add_argument("--host", default=os.getenv("VOICE_API_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.getenv("VOICE_API_PORT", DEFAULT_PORT)))
    return parser


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Local Voice API listening on http://{args.host}:{args.port}")
    print("POST /speak with JSON: {\"text\":\"...\", \"voice\":\"your_voice_id\", \"device\":\"BlackHole 2ch\"}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
