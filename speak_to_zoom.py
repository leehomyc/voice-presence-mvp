#!/usr/bin/env python3
"""Stream ElevenLabs TTS into a local output device such as BlackHole."""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
import wave
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import requests
import sounddevice as sd
from dotenv import load_dotenv


API_BASE = "https://api.elevenlabs.io/v1"
DEFAULT_MODEL_ID = "eleven_flash_v2_5"
DEFAULT_OUTPUT_FORMAT = "pcm_24000"
VIRTUAL_DEVICE_HINTS = ("BlackHole", "Loopback", "CABLE", "VB-Audio")


@dataclass(frozen=True)
class Config:
    api_key: str
    voice: str
    model_id: str
    output_device: str | None


def load_config(args: argparse.Namespace) -> Config:
    load_dotenv()
    api_key = args.api_key or os.getenv("ELEVENLABS_API_KEY", "")
    voice = (
        args.voice
        or getattr(args, "voice_id", None)
        or os.getenv("ELEVENLABS_VOICE")
        or os.getenv("ELEVENLABS_VOICE_ID", "")
    )
    model_id = args.model_id or os.getenv("ELEVENLABS_MODEL_ID", DEFAULT_MODEL_ID)
    output_device = args.device or os.getenv("ELEVENLABS_OUTPUT_DEVICE") or None
    return Config(
        api_key=api_key.strip(),
        voice=voice.strip(),
        model_id=model_id.strip(),
        output_device=output_device.strip() if output_device else None,
    )


def require_api_config(config: Config) -> None:
    missing = []
    if not config.api_key:
        missing.append("ELEVENLABS_API_KEY")
    if not config.voice:
        missing.append("ELEVENLABS_VOICE")
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(f"Missing {joined}. Add it to .env or pass it as an argument.")


def sample_rate_from_output_format(output_format: str) -> int:
    parts = output_format.split("_")
    if len(parts) < 2 or parts[0] != "pcm":
        raise SystemExit("This MVP expects a PCM output format such as pcm_24000.")
    try:
        return int(parts[1])
    except ValueError as exc:
        raise SystemExit(f"Could not parse sample rate from {output_format!r}.") from exc


def list_devices() -> None:
    print("Audio devices:")
    for device_info in get_audio_devices():
        print(
            f"{device_info['index']:>3}  "
            f"in:{device_info['input_channels']:<2} "
            f"out:{device_info['output_channels']:<2} "
            f"rate:{device_info['default_samplerate']:<5}  "
            f"{device_info['name']}"
        )


def get_audio_devices() -> list[dict[str, Any]]:
    devices = sd.query_devices()
    result = []
    for index, device in enumerate(devices):
        in_ch = int(device.get("max_input_channels", 0))
        out_ch = int(device.get("max_output_channels", 0))
        default_rate = int(float(device.get("default_samplerate", 0)))
        result.append(
            {
                "index": index,
                "name": str(device["name"]),
                "input_channels": in_ch,
                "output_channels": out_ch,
                "default_samplerate": default_rate,
            }
        )
    return result


def output_device_candidates() -> list[tuple[int, str]]:
    devices = sd.query_devices()
    candidates = []
    for index, device in enumerate(devices):
        if int(device.get("max_output_channels", 0)) > 0:
            candidates.append((index, str(device["name"])))
    return candidates


def resolve_output_device(name_or_index: str | None) -> int | None:
    candidates = output_device_candidates()
    if not name_or_index:
        for hint in VIRTUAL_DEVICE_HINTS:
            for index, name in candidates:
                if hint.lower() in name.lower():
                    return index
        return None

    try:
        index = int(name_or_index)
    except ValueError:
        index = -1
    if index >= 0:
        device = sd.query_devices(index)
        if int(device.get("max_output_channels", 0)) <= 0:
            raise SystemExit(f"Device {index} is not an output device.")
        return index

    matches = [(idx, name) for idx, name in candidates if name_or_index.lower() in name.lower()]
    if not matches:
        raise SystemExit(f"No output device matched {name_or_index!r}. Run list-devices.")
    if len(matches) > 1:
        print("Multiple output devices matched; using the first:")
        for idx, name in matches:
            print(f"  {idx}: {name}")
    return matches[0][0]


def get_output_channels(device_index: int | None, requested_channels: int) -> int:
    if requested_channels in (1, 2):
        return requested_channels
    raise SystemExit("--channels must be 1 or 2.")


def fetch_voices(api_key: str) -> list[dict[str, Any]]:
    response = requests.get(
        f"{API_BASE}/voices",
        headers={"xi-api-key": api_key.strip()},
        timeout=30,
    )
    if response.status_code >= 400:
        raise SystemExit(f"Could not list voices: {response.status_code}\n{response.text[:1200]}")
    return response.json().get("voices", [])


def resolve_voice_id(api_key: str, voice: str) -> str:
    voice = voice.strip()
    if not voice:
        raise SystemExit("Missing ELEVENLABS_VOICE.")
    voices = fetch_voices(api_key)
    exact_id = [item for item in voices if item.get("voice_id") == voice]
    if exact_id:
        return str(exact_id[0]["voice_id"])

    voice_lower = voice.lower()
    exact_name = [item for item in voices if str(item.get("name", "")).lower() == voice_lower]
    if len(exact_name) == 1:
        return str(exact_name[0]["voice_id"])

    partial = [
        item
        for item in voices
        if voice_lower in str(item.get("name", "")).lower()
        or voice_lower in str(item.get("voice_id", "")).lower()
    ]
    if len(partial) == 1:
        return str(partial[0]["voice_id"])
    if len(partial) > 1:
        options = "\n".join(
            f"  {item.get('voice_id')}  {item.get('name', 'unnamed')}" for item in partial
        )
        raise SystemExit(f"Voice {voice!r} matched multiple voices:\n{options}")
    raise SystemExit(f"Voice {voice!r} was not found in your ElevenLabs voices.")


def elevenlabs_pcm_stream(
    config: Config,
    text: str,
    output_format: str,
    stability: float,
    similarity_boost: float,
    speed: float,
) -> Iterable[bytes]:
    voice_id = resolve_voice_id(config.api_key, config.voice)
    url = f"{API_BASE}/text-to-speech/{voice_id}/stream"
    headers = {
        "xi-api-key": config.api_key,
        "Content-Type": "application/json",
        "Accept": "application/octet-stream",
    }
    params = {"output_format": output_format}
    payload = {
        "text": text,
        "model_id": config.model_id,
        "voice_settings": {
            "stability": stability,
            "similarity_boost": similarity_boost,
            "style": 0.0,
            "use_speaker_boost": True,
            "speed": speed,
        },
    }

    with requests.post(
        url,
        headers=headers,
        params=params,
        json=payload,
        stream=True,
        timeout=(15, 120),
    ) as response:
        if response.status_code >= 400:
            detail = response.text[:1200]
            raise SystemExit(f"ElevenLabs request failed: {response.status_code}\n{detail}")
        for chunk in response.iter_content(chunk_size=4096):
            if chunk:
                yield chunk


def write_wav(path: str, pcm_chunks: list[bytes], sample_rate: int) -> None:
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        for chunk in pcm_chunks:
            wav.writeframes(chunk)


def play_pcm_stream(
    chunks: Iterable[bytes],
    sample_rate: int,
    device_index: int | None,
    channels: int,
    save_wav: str | None,
    gain: float = 1.0,
) -> None:
    saved_chunks: list[bytes] = []
    carry = b""
    with sd.OutputStream(
        samplerate=sample_rate,
        channels=channels,
        dtype="int16",
        device=device_index,
        blocksize=0,
    ) as stream:
        for chunk in chunks:
            if save_wav:
                saved_chunks.append(chunk)
            data = carry + chunk
            usable = len(data) - (len(data) % 2)
            carry = data[usable:]
            if usable <= 0:
                continue
            mono = np.frombuffer(data[:usable], dtype="<i2")
            if mono.size == 0:
                continue
            if gain != 1.0:
                boosted = mono.astype(np.float32) * gain
                mono = np.clip(boosted, -32768, 32767).astype(np.int16)
            frame = mono if channels == 1 else np.column_stack((mono, mono))
            stream.write(frame)
    if save_wav:
        write_wav(save_wav, saved_chunks, sample_rate)


def command_voices(args: argparse.Namespace) -> None:
    load_dotenv()
    api_key = args.api_key or os.getenv("ELEVENLABS_API_KEY", "")
    if not api_key.strip():
        raise SystemExit("Missing ELEVENLABS_API_KEY. Add it to .env or pass --api-key.")
    voices = fetch_voices(api_key)
    if not voices:
        print("No voices returned.")
        return
    for voice in voices:
        category = voice.get("category", "unknown")
        name = voice.get("name", "unnamed")
        voice_id = voice.get("voice_id", "")
        print(f"{voice_id}  {name}  ({category})")


def command_say(args: argparse.Namespace) -> None:
    config = load_config(args)
    require_api_config(config)
    text = args.text if args.text is not None else sys.stdin.read()
    text = text.strip()
    if not text:
        raise SystemExit("No text provided. Use --text or pipe text into the command.")

    sample_rate = sample_rate_from_output_format(args.output_format)
    device_index = resolve_output_device(config.output_device)
    channels = get_output_channels(device_index, args.channels)
    if device_index is None:
        print("No virtual output device selected; using the system default output.")
    else:
        print(f"Output device: {device_index} - {sd.query_devices(device_index)['name']}")

    chunks = elevenlabs_pcm_stream(
        config=config,
        text=text,
        output_format=args.output_format,
        stability=args.stability,
        similarity_boost=args.similarity_boost,
        speed=args.speed,
    )
    play_pcm_stream(chunks, sample_rate, device_index, channels, args.save_wav, args.gain)


def command_repl(args: argparse.Namespace) -> None:
    config = load_config(args)
    require_api_config(config)
    sample_rate = sample_rate_from_output_format(args.output_format)
    device_index = resolve_output_device(config.output_device)
    channels = get_output_channels(device_index, args.channels)
    if device_index is None:
        print("No virtual output device selected; using the system default output.")
    else:
        print(f"Output device: {device_index} - {sd.query_devices(device_index)['name']}")
    print("Type a sentence, then Enter. Commands: /quit, /devices")

    while True:
        try:
            text = input("speak> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        if text in {"/q", "/quit", "/exit"}:
            return
        if text == "/devices":
            list_devices()
            continue
        chunks = elevenlabs_pcm_stream(
            config=config,
            text=text,
            output_format=args.output_format,
            stability=args.stability,
            similarity_boost=args.similarity_boost,
            speed=args.speed,
        )
        play_pcm_stream(chunks, sample_rate, device_index, channels, None, args.gain)


def command_test_device(args: argparse.Namespace) -> None:
    sample_rate = args.sample_rate
    device_index = resolve_output_device(args.device)
    channels = get_output_channels(device_index, args.channels)
    duration = args.seconds
    frequency = args.frequency
    frames = int(sample_rate * duration)
    t = np.arange(frames, dtype=np.float32) / sample_rate
    wave_data = 0.15 * np.sin(2 * math.pi * frequency * t)
    mono = np.int16(wave_data * np.iinfo(np.int16).max)
    audio = mono if channels == 1 else np.column_stack((mono, mono))
    if device_index is None:
        print("No virtual output device selected; using the system default output.")
    else:
        print(f"Testing output device: {device_index} - {sd.query_devices(device_index)['name']}")
    sd.play(audio, samplerate=sample_rate, device=device_index)
    time.sleep(duration + 0.1)
    sd.stop()


def add_common_api_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api-key", help="ElevenLabs API key. Defaults to ELEVENLABS_API_KEY.")
    parser.add_argument(
        "--voice",
        "--voice-id",
        dest="voice",
        help="ElevenLabs voice name, partial ID, or full ID. Defaults to ELEVENLABS_VOICE.",
    )
    parser.add_argument(
        "--model-id",
        default=None,
        help=f"ElevenLabs model ID. Defaults to ELEVENLABS_MODEL_ID or {DEFAULT_MODEL_ID}.",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Output device name/index. Defaults to ELEVENLABS_OUTPUT_DEVICE or a virtual device.",
    )


def add_voice_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output-format",
        default=DEFAULT_OUTPUT_FORMAT,
        help=f"ElevenLabs PCM output format. Default: {DEFAULT_OUTPUT_FORMAT}.",
    )
    parser.add_argument("--channels", type=int, default=2, help="Output channels: 1 or 2. Default: 2.")
    parser.add_argument("--stability", type=float, default=0.35)
    parser.add_argument("--similarity-boost", type=float, default=0.9)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--gain", type=float, default=1.0, help="Linear playback gain. Default: 1.0.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stream ElevenLabs speech into BlackHole/Loopback.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-devices", help="List local audio devices.")
    list_parser.set_defaults(func=lambda _args: list_devices())

    voices_parser = subparsers.add_parser("voices", help="List ElevenLabs voices in your account.")
    voices_parser.add_argument("--api-key", help="ElevenLabs API key. Defaults to ELEVENLABS_API_KEY.")
    voices_parser.set_defaults(func=command_voices)

    say_parser = subparsers.add_parser("say", help="Speak one text string or stdin.")
    add_common_api_args(say_parser)
    add_voice_args(say_parser)
    say_parser.add_argument("--text", help="Text to speak. If omitted, reads stdin.")
    say_parser.add_argument("--save-wav", help="Optionally save the mono PCM stream as a WAV file.")
    say_parser.set_defaults(func=command_say)

    repl_parser = subparsers.add_parser("repl", help="Interactive type-to-speak loop.")
    add_common_api_args(repl_parser)
    add_voice_args(repl_parser)
    repl_parser.set_defaults(func=command_repl)

    test_parser = subparsers.add_parser("test-device", help="Play a short tone to the output device.")
    test_parser.add_argument("--device", default=None, help="Output device name/index.")
    test_parser.add_argument("--channels", type=int, default=2, help="Output channels: 1 or 2. Default: 2.")
    test_parser.add_argument("--sample-rate", type=int, default=24000)
    test_parser.add_argument("--seconds", type=float, default=0.7)
    test_parser.add_argument("--frequency", type=float, default=660.0)
    test_parser.set_defaults(func=command_test_device)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
