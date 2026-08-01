# Fish Audio TTS plugin for Hermes

This user plugin connects Hermes' `TTSProvider` hook to
[EpicIsTheOne/fish-audio-tts-toolkit](https://github.com/EpicIsTheOne/fish-audio-tts-toolkit).
The toolkit provides Fish voice synthesis, narration cleanup, and automatic
emotion tagging. Hermes starts the toolkit helper lazily for the first TTS
request, which makes it suitable for the wake-word flow.

## Installed layout

```text
~/.hermes/plugins/tts/fishaudio/
  __init__.py
  plugin.yaml
~/.hermes/plugins/fishaudio-toolkit/  # cloned from EpicIsTheOne/fish-audio-tts-toolkit
```

On this Windows installation the equivalent path is:

```text
C:\Users\Epic\AppData\Local\hermes\plugins\tts\fishaudio\
C:\Users\Epic\AppData\Local\hermes\plugins\fishaudio-toolkit\
```

## Setup

Keep the Fish key in Hermes' local `.env` or in the toolkit's `.env`; never put
it in `config.yaml` or chat:

```text
FISH_AUDIO_API_KEY=...
```

The toolkit's `.env` can also set `DEFAULT_FISH_REFERENCE_ID`, but the preferred
Hermes config is:

```yaml
tts:
  provider: fishaudio
  fishaudio:
    voice: "YOUR_FISH_REFERENCE_ID"
    model: s2-pro
    latency: low
    output_format: mp3
    include_asterisk_narration: false
    auto_start: true
    helper_url: http://127.0.0.1:3027
```

`voice` is a Fish Audio model/reference ID. The toolkit auto-tags delivery cues
such as whispers, laughs, teasing, and calm/serious tones before synthesis.

Enable the plugin and restart Hermes/gateway:

```bash
hermes plugins enable tts/fishaudio
```

Wake mode can then use the same provider:

```text
/wake on
```

or, in config:

```yaml
wake_word:
  enabled: true
```

The first spoken reply starts the local toolkit helper with `npm start`. If you
prefer to run it yourself, use:

```bash
cd ~/.hermes/plugins/fishaudio-toolkit
npm start
```

On Windows, `cd` to the equivalent `C:\Users\Epic\AppData\Local\hermes\...`
path. The helper is loopback-only by default.

## Helper authentication

If `FISH_HELPER_API_KEY` is set for the toolkit, set the same secret in Hermes'
`.env`. The plugin forwards it as a bearer token to the localhost helper.

## Updating the toolkit

The checkout is a shallow clone of the `master` branch. Update it with:

```bash
cd ~/.hermes/plugins/tts/fishaudio/toolkit
git pull --ff-only origin master
npm install
```
