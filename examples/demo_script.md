# Demo Script

Use this for a short Zoom or Google Meet demo where one side talks to the AI voice agent.

## Setup Shot

- Meeting app is open.
- Microphone is the virtual mic, for example `BlackHole 2ch`.
- Speaker is routed into capture, for example `BlackHole 16ch`.
- Local API is open at `http://127.0.0.1:8765`.

## Conversation

**You:** Hi, can you hear me?

**AI voice:** Yes, I can hear you.

**You:** Who are you?

**AI voice:** I am a local voice-agent prototype. I listen to the call, draft a short reply, and speak it back through a cloned voice.

**You:** Where are you right now?

**AI voice:** I can answer from the context file if location is configured. Otherwise I should say I do not know instead of making one up.

**You:** What kind of research or work context can you use?

**AI voice:** It can use a local memory file, style notes, meeting transcript, pasted context, and calendar or Notion summaries if those are added to the context pack.

**You:** Could this join Zoom or Google Meet?

**AI voice:** Yes. The practical trick is audio routing: meeting audio goes into transcription, and generated speech comes back through a virtual microphone.

**You:** What are the hard parts?

**AI voice:** Latency, voice naturalness, picking the right memory, and the social rules around when an AI voice is appropriate.

**You:** What would you use it for?

**AI voice:** Meeting copilot workflows, boring attendance calls, simple replies, and customer service calls to complain or dispute something.

**You:** What is still obviously MVP?

**AI voice:** Some phonetics are off, long replies can sound robotic, and the context can be wrong if the wrong memory is retrieved.

## Close

**You:** Give me the one-sentence version.

**AI voice:** It is a local digital voice presence MVP that can listen to a meeting, generate a short answer, and speak it back through your voice.
