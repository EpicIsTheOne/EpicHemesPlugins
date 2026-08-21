# Discord model picker paginator for Hermes

Fixes Hermes' Discord `/model` picker so every model is reachable, and keeps
brand-new OpenRouter models selectable without waiting for a Hermes release.

## What it does

1. **Pagination.** Discord hard-caps one select menu at 25 options, and the
   bundled `ModelPickerView` silently truncated the model list there. This
   plugin replaces the view with a paginated variant:
   - 25 models per page with ◀ Prev / Next ▶ buttons
   - "Page X/Y" placeholder so you know where you are
   - The provider step is paginated the same way (only when a provider list
     exceeds 25 entries, so normal setups see no change)
   - Auth gating, expensive-model confirm, Back/Cancel, and timeout semantics
     are preserved

2. **New-model injection.** The picker list comes from Hermes' curated
   OpenRouter catalog, which can lag freshly released models. The plugin
   verifies against OpenRouter's live `/v1/models` that a model exists and
   supports tool calling, then appends it to the picker. Currently tracked:
   `stealth/ox-alpha`. If OpenRouter is unreachable, it falls back to the
   profile-local `models_dev_cache.json` mirror.

3. **Namespaced-adapter safety.** Hermes loads bundled platform adapters under
   two different Python namespaces (`plugins.platforms.*` and the gateway's
   `hermes_plugins.*`). They are separate module objects, so the plugin
   patches every loaded copy of the Discord adapter, matching by source path,
   and re-applies after lazy re-initialization.

## Why a plugin

Editing `plugins/platforms/discord/adapter.py` in the Hermes checkout gets
wiped by the next `hermes update`. A user plugin in `~/.hermes/plugins/`
re-applies on every gateway boot regardless of Hermes version.

## Install

```bash
mkdir -p ~/.hermes/plugins/discord-model-paginator
cp __init__.py plugin.yaml ~/.hermes/plugins/discord-model-paginator/
hermes plugins enable discord-model-paginator
hermes gateway restart   # or /restart from a chat surface
```

## Verify

```bash
hermes plugins show discord-model-paginator
hermes plugins doctor ~/.hermes/plugins/discord-model-paginator --ci
```

Then open `/model` on Discord: a provider with more than 25 models now shows
Prev/Next paging, and newly released OpenRouter models appear on the last page.

## Files

- `plugin.yaml` — manifest
- `__init__.py` — the pagination view, catalog patch, and `register(ctx)`
