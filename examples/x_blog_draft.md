# X / Blog Draft

I have too many meetings.

Travel, conflicts, timezone differences, random errands, and calendar pileups make it surprisingly easy to miss one. And not every call deserves full human energy either. Some are boring. Some are awkward. Some are just painful, like calling customer service to complain or dispute something.

So I built a small digital voice presence MVP.

The goal was simple: can an AI listen to a live call, generate a reasonable reply, and speak back in my voice through Zoom?

The first version works.

The pipeline is:

```text
Zoom / Meet / phone call audio
-> speech-to-text
-> LLM with local context + memory
-> ElevenLabs streaming TTS
-> virtual microphone
-> back into the call
```

In practice, this means the same local agent can be routed into Zoom, Google Meet, WeChat, WhatsApp, or basically any app that lets you choose a microphone/speaker device.

I built most of it while taking a walk outside, using Codex to drive the implementation on my laptop.

The funny part is that the “AI” part was not the only hard part.

The annoying parts were much more physical:

- routing audio between apps
- avoiding echo
- getting Zoom speaker audio into transcription
- sending generated voice back through a virtual mic
- making replies short enough to feel conversational
- keeping latency low enough that it does not feel dead

The software stack is pretty compact:

- OpenAI for transcription and reply generation
- ElevenLabs for low-latency voice generation
- BlackHole / Loopback style virtual audio routing
- a tiny local Python API
- local memory, style, transcript, and context files

The biggest challenge is memory.

A meeting agent that only answers the last sentence feels shallow. A meeting agent that stuffs in every memory becomes confused, slow, or weirdly over-specific.

The useful middle ground is retrieval:

- current utterance first
- recent transcript second
- meeting context third
- long-term memory only when relevant
- never volunteer private facts unless asked

The other challenge is the uncanny valley.

When it works, some sentences sound almost exactly like me. Friends hear it and immediately go, “wait, that is actually scary.”

Then ten seconds later they notice something:

- one phonetic is off
- one word sounds robotic
- the pacing is slightly strange
- the reply is too generic
- the context retrieval grabs the wrong thing

That gap is the whole product question.

The MVP is already useful for:

- meeting copilot workflows
- simple attendance and acknowledgement calls
- boring status calls
- customer service calls to complain or dispute
- difficult conversations where a prepared voice assistant can help keep the tone steady
- live demos of personal-context agents

But the social layer is harder than the technical layer.

In a corporate setting, an AI voice attending meetings can easily feel rude, deceptive, or disrespectful. The right UX probably needs clear disclosure, controls, audit logs, and meeting norms that people actually accept.

That said, the use case still feels strong.

There are many calls where the value is not deep human presence. It is listening, remembering, giving short replies, asking the next question, or making sure nothing falls through the cracks.

This prototype is rough, but it made the future feel much closer:

a meeting agent that can listen, remember, speak, and be present wherever audio calls happen.

Code: https://github.com/leehomyc/voice-presence-mvp

Demo: add the video link here after uploading the Zoom recording clip.
