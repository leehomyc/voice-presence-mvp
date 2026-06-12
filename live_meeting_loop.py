#!/usr/bin/env python3
"""Local live meeting loop: mic -> OpenAI transcription -> reply -> ElevenLabs mic."""

from __future__ import annotations

import argparse
import os
import queue
import tempfile
import time
import wave
from collections import deque
from typing import Any

import numpy as np
import requests
import sounddevice as sd
from dotenv import load_dotenv


DEFAULT_API = "http://127.0.0.1:8765"


def resolve_input_device(name: str | None) -> int | None:
    if not name:
        return None
    needle = name.lower()
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0 and needle in str(device["name"]).lower():
            return index
    raise ValueError(f"No input device matched {name!r}.")


def rms(block: np.ndarray) -> float:
    if block.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(block.astype(np.float32)))))


def write_wav(path: str, audio: np.ndarray, sample_rate: int) -> None:
    clipped = np.clip(audio, -1.0, 1.0)
    pcm = (clipped * 32767).astype(np.int16)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def transcribe(
    path: str,
    api_key: str,
    model: str,
    language: str | None,
    prompt: str | None,
    response_format: str,
) -> str:
    data: dict[str, str] = {"model": model, "response_format": response_format}
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt
    with open(path, "rb") as handle:
        response = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            data=data,
            files={"file": ("utterance.wav", handle, "audio/wav")},
            timeout=60,
    )
    response.raise_for_status()
    if response_format == "text":
        return response.text.strip()
    return str(response.json().get("text", "")).strip()


def post_json(url: str, payload: dict[str, Any], timeout: int = 60) -> dict[str, Any]:
    response = requests.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def run_loop(args: argparse.Namespace) -> None:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY in .env.")

    input_device = resolve_input_device(args.input_device)
    sample_rate = args.sample_rate
    block_frames = int(sample_rate * args.block_ms / 1000)
    silence_blocks_needed = max(1, int(args.silence_ms / args.block_ms))
    min_blocks_needed = max(1, int(args.min_ms / args.block_ms))
    max_blocks = max(1, int(args.max_seconds * 1000 / args.block_ms))
    preroll = deque(maxlen=max(1, int(args.preroll_ms / args.block_ms)))
    audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=args.queue_blocks)

    def audio_callback(indata: np.ndarray, _frames: int, _time_info: Any, _status: Any) -> None:
        block = indata[:, 0].copy()
        try:
            audio_queue.put_nowait(block)
        except queue.Full:
            try:
                audio_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                audio_queue.put_nowait(block)
            except queue.Full:
                pass

    print("Live meeting loop running.")
    print(f"Input device: {args.input_device or 'system default'}")
    print(f"Zoom mic output device: {args.output_device}")
    print(f"Transcription model: {args.transcribe_model}")
    print("Tip: set Zoom Speaker to BlackHole 16ch for direct Zoom audio capture.")

    recording = False
    silence_blocks = 0
    frames: list[np.ndarray] = []
    last_spoke_at = 0.0

    with sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        device=input_device,
        blocksize=block_frames,
        callback=audio_callback,
    ) as stream:
        while True:
            block = audio_queue.get()
            block = block.reshape(-1)
            level = rms(block)
            now = time.time()
            in_cooldown = now - last_spoke_at < args.cooldown_seconds

            if not recording:
                preroll.append(block.copy())
                if not in_cooldown and level >= args.threshold:
                    recording = True
                    silence_blocks = 0
                    frames = [b.copy() for b in preroll]
                    print(f"Heard voice, recording... level={level:.4f}")
                continue

            frames.append(block.copy())
            if level < args.threshold * args.silence_ratio:
                silence_blocks += 1
            else:
                silence_blocks = 0

            long_enough = len(frames) >= min_blocks_needed
            silent_enough = silence_blocks >= silence_blocks_needed
            too_long = len(frames) >= max_blocks
            if not ((long_enough and silent_enough) or too_long):
                continue

            recording = False
            audio = np.concatenate(frames, axis=0)
            frames = []
            duration = len(audio) / sample_rate
            if duration < args.min_ms / 1000:
                continue

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                wav_path = tmp.name
            try:
                write_wav(wav_path, audio, sample_rate)
                text = transcribe(
                    wav_path,
                    api_key,
                    args.transcribe_model,
                    args.language,
                    args.transcribe_prompt,
                    args.transcribe_response_format,
                )
            finally:
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass

            if not text:
                print("Transcription was empty.")
                continue
            print(f"Remote: {text}")
            post_json(f"{args.api}/transcript", {"speaker": args.speaker, "text": text}, timeout=15)
            reply = post_json(
                f"{args.api}/draft-reply",
                {
                    "speaker": args.speaker,
                    "text": text,
                    "instruction": args.instruction,
                    "context": args.live_context,
                    "speak": True,
                    "voice": args.voice,
                    "device": args.output_device,
                    "gain": args.gain,
                    "speed": args.tts_speed,
                    "openai_model": args.openai_model,
                    "memory_mode": args.memory_mode,
                    "max_output_tokens": args.reply_tokens,
                },
                timeout=90,
            )
            meta = ""
            if isinstance(reply, dict) and reply.get("prompt_chars"):
                meta = f" [model={reply.get('model')} memory={reply.get('memory_mode')} prompt_chars={reply.get('prompt_chars')}]"
            print(f"Assistant: {reply.get('reply', reply)}{meta}")
            last_spoke_at = time.time()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a live Zoom voice-agent loop.")
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--input-device", default=os.getenv("LIVE_INPUT_DEVICE", "MacBook Pro Microphone"))
    parser.add_argument("--output-device", default=os.getenv("ELEVENLABS_OUTPUT_DEVICE", "BlackHole 2ch"))
    parser.add_argument("--voice", default=os.getenv("ELEVENLABS_VOICE", ""))
    parser.add_argument("--gain", type=float, default=float(os.getenv("LIVE_OUTPUT_GAIN", "1")))
    parser.add_argument("--speaker", default="remote")
    parser.add_argument(
        "--instruction",
        default=(
            "Reply naturally for a live meeting conversation. Use current facts and memory only when relevant. "
            "Do not proactively mention location; mention it only if asked or if travel/location is clearly relevant. "
            "Do not invent private life details."
        ),
    )
    parser.add_argument(
        "--live-context",
        default=os.getenv(
            "LIVE_CURRENT_CONTEXT",
            "This is a live Zoom voice-agent meeting test.",
        ),
    )
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--block-ms", type=int, default=100)
    parser.add_argument("--threshold", type=float, default=0.012)
    parser.add_argument("--silence-ratio", type=float, default=0.55)
    parser.add_argument("--tts-speed", type=float, default=float(os.getenv("LIVE_TTS_SPEED", "1.0")))
    parser.add_argument("--silence-ms", type=int, default=950)
    parser.add_argument("--min-ms", type=int, default=550)
    parser.add_argument("--max-seconds", type=float, default=18.0)
    parser.add_argument("--preroll-ms", type=int, default=350)
    parser.add_argument("--cooldown-seconds", type=float, default=0.8)
    parser.add_argument("--queue-blocks", type=int, default=900)
    parser.add_argument("--reply-tokens", type=int, default=150)
    parser.add_argument("--openai-model", default=os.getenv("LIVE_OPENAI_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini")))
    parser.add_argument("--memory-mode", choices=["large", "full"], default=os.getenv("LIVE_MEMORY_MODE", "large"))
    parser.add_argument("--transcribe-model", default=os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"))
    parser.add_argument("--transcribe-response-format", default=os.getenv("OPENAI_TRANSCRIBE_RESPONSE_FORMAT", "text"))
    parser.add_argument(
        "--transcribe-prompt",
        default=os.getenv(
            "OPENAI_TRANSCRIBE_PROMPT",
            "Live meeting conversation. Common words: Zoom, Google Meet, BlackHole, ElevenLabs, OpenAI, research, meeting, customer service, complaint, dispute.",
        ),
    )
    parser.add_argument("--language", default=os.getenv("OPENAI_TRANSCRIBE_LANGUAGE", ""))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.language = args.language or None
    run_loop(args)


if __name__ == "__main__":
    main()
