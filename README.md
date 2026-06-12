# Voice Presence MVP

A local MVP for routing an AI-generated voice into live calls.

```text
meeting audio -> transcription -> LLM reply -> ElevenLabs voice -> virtual mic -> Zoom/Meet/calling app
```

It can also run in a simpler push-to-speak mode:

```text
typed text -> ElevenLabs voice -> virtual audio device -> meeting microphone
```

Use only voices you own or have permission to use. For real meetings and calls, disclose AI voice use where appropriate.

## What It Does

- Speaks arbitrary typed text through an ElevenLabs voice.
- Exposes a local browser UI and HTTP API at `http://127.0.0.1:8765`.
- Captures meeting audio from a virtual speaker device.
- Transcribes speech with OpenAI audio transcription.
- Drafts short live replies with an OpenAI model.
- Sends the reply back into Zoom, Google Meet, WeChat, WhatsApp, or other apps through a virtual microphone.
- Stores local transcripts in `transcripts/` for debugging and optional later Notion workflows.

## Requirements

- macOS, Windows, or Linux with a working Python 3.10+ install.
- An ElevenLabs API key and voice ID/name.
- An OpenAI API key.
- A virtual audio driver:
  - macOS free route: BlackHole 2ch/16ch
  - macOS paid route: Loopback or Audio Hijack
  - Windows route: VB-CABLE or VoiceMeeter

## Setup

```bash
git clone https://github.com/leehomyc/voice-presence-mvp.git
cd voice-presence-mvp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```bash
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE=your_voice_id_or_voice_name
ELEVENLABS_OUTPUT_DEVICE=BlackHole 2ch
OPENAI_API_KEY=...
LIVE_INPUT_DEVICE=BlackHole 16ch
```

Optional personal context files:

```bash
VOICE_PERSONA_NAME=Your Name
VOICE_STYLE_FILE=/absolute/path/to/style-notes.md
VOICE_MEMORY_FILE=/absolute/path/to/memory-notes.md
VOICE_WORKSPACE_ROOT=/absolute/path/to/context-folder
```

## Find Devices And Voices

List ElevenLabs voices:

```bash
python speak_to_zoom.py voices
```

List audio devices:

```bash
python speak_to_zoom.py list-devices
```

Test audio into the virtual microphone path:

```bash
python speak_to_zoom.py test-device --device "BlackHole 2ch"
```

## Typed Speech Mode

```bash
python speak_to_zoom.py repl --device "BlackHole 2ch"
```

In Zoom or Google Meet, set microphone to `BlackHole 2ch` or the matching virtual cable. Type a sentence, press Enter, and the generated voice is sent into the call.

One-off speech:

```bash
python speak_to_zoom.py say \
  --text "I agree with that direction. Let me add one quick point." \
  --device "BlackHole 2ch"
```

## Local Browser API

Start the API:

```bash
python local_voice_api.py
```

Open:

```text
http://127.0.0.1:8765
```

Speak from another app:

```bash
curl -X POST http://127.0.0.1:8765/speak \
  -H "Content-Type: application/json" \
  -d '{"text":"I agree with that direction.", "device":"BlackHole 2ch"}'
```

Useful checks:

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/devices
curl http://127.0.0.1:8765/voices
```

The API binds to `127.0.0.1` by default, so it is only reachable from the local machine.

## Live Meeting Loop

For direct Zoom capture on macOS, one working routing pattern is:

```text
Zoom speaker -> BlackHole 16ch -> live_meeting_loop.py
AI voice -> BlackHole 2ch -> Zoom microphone
```

Run:

```bash
python live_meeting_loop.py \
  --input-device "BlackHole 16ch" \
  --output-device "BlackHole 2ch" \
  --tts-speed 1.0
```

Then set the meeting app:

- Speaker: `BlackHole 16ch`
- Microphone: `BlackHole 2ch`

Use headphones or a monitor route if you also need to hear the meeting locally. Audio routing is the fiddliest part of the MVP.

## Context And Memory

The reply endpoint can use:

- recent transcript lines from the current server session
- short context pasted into the local UI
- an optional style file
- an optional memory file
- an optional context pack at `context/current_context_pack.md`

These files are local-only and ignored by Git by default.

## Notes

- Low latency needs streaming end to end: short utterance detection, fast transcription, compact LLM replies, and ElevenLabs flash TTS.
- Long answers sound less natural and increase call awkwardness. One or two sentences usually work best.
- The uncanny valley is real: some sentences can sound very close to the target voice, while some phonetics and prosody still reveal the system.
- The social boundary matters. In many corporate settings, undisclosed AI meeting attendance can be rude or inappropriate.
