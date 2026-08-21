# Epic Hermes Plugins

Standalone Hermes plugins maintained by EpicIsTheOne.

## Included

- `plugins/tts/fishaudio/` — Fish Audio TTS provider for Hermes.
  It uses the external [`fish-audio-tts-toolkit`](https://github.com/EpicIsTheOne/fish-audio-tts-toolkit)
  checkout for narration cleanup, emotion tagging, Fish synthesis, and voice search.
- `plugins/discord-model-paginator/` — Discord `/model` picker fix.
  Adds Prev/Next pagination past Discord's 25-option select-menu cap and keeps
  newly released OpenRouter models (e.g. `stealth/ox-alpha`) selectable.

## Installation

Clone this repository, then copy or symlink `plugins/tts/fishaudio/` into:

```text
~/.hermes/plugins/tts/fishaudio/
```

Install the toolkit separately from its own repository and configure the Fish API
key locally in `.env`. Never commit credentials, `.env` files, `node_modules`, or
generated audio.
