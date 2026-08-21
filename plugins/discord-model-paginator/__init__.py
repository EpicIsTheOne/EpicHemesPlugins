"""Discord model-picker paginator (Nyxie patch for Epic).

Discord select menus are hard-capped at 25 options.  Hermes' bundled
``DiscordAdapter.ModelPickerView`` silently truncates ``models[:25]``, so on a
provider with hundreds/thousands of models (e.g. OpenRouter) most are
unreachable from the Discord ``/model`` dropdown.

This user plugin replaces the ``ModelPickerView`` global with a paginated
variant: the model step shows 25 models per page with ◀ Prev / Next ▶ buttons
and a "Page X/Y" placeholder, so every model is selectable.  It re-applies on
every gateway boot (and survives ``hermes update`` because it lives in
``~/.hermes/plugins/``), and wraps ``_define_discord_view_classes`` so a lazy
Discord re-init can't silently undo it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import urllib.request
from functools import wraps

logger = logging.getLogger("hermes.plugins.discord-model-paginator")

PAGE_SIZE = 25  # Discord select hard cap
OX_ALPHA_MODEL = "stealth/ox-alpha"
_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_ox_alpha_available: bool | None = None
_ox_alpha_probe_lock = threading.Lock()


def _catalog_fallback_for_ox_alpha() -> bool | None:
    """Read the profile-local models.dev mirror without doing network I/O."""
    try:
        from hermes_constants import get_hermes_home

        path = get_hermes_home() / "models_dev_cache.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        item = (
            payload.get("openrouter", {})
            .get("models", {})
            .get(OX_ALPHA_MODEL)
        )
        if isinstance(item, dict):
            return bool(
                item.get("tool_call")
                or item.get("tool_calling")
                or item.get("supports_tools")
            )
    except Exception:
        pass
    return None


def _ox_alpha_is_available() -> bool:
    """Return whether OpenRouter currently exposes a callable Ox Alpha model."""
    global _ox_alpha_available
    if _ox_alpha_available is not None:
        return _ox_alpha_available

    with _ox_alpha_probe_lock:
        if _ox_alpha_available is not None:
            return _ox_alpha_available
        try:
            request = urllib.request.Request(
                _OPENROUTER_MODELS_URL,
                headers={"Accept": "application/json", "User-Agent": "Hermes discord-model-paginator"},
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
            item = next(
                (
                    candidate
                    for candidate in payload.get("data", [])
                    if isinstance(candidate, dict)
                    and candidate.get("id") == OX_ALPHA_MODEL
                ),
                None,
            )
            supported = set(item.get("supported_parameters") or ()) if item else set()
            _ox_alpha_available = bool(
                item
                and ("tools" in supported or item.get("tool_call") is True)
            )
        except Exception:
            # Keep the plugin useful offline if the local model catalog already
            # knows this model. A live negative response is authoritative; only
            # transport/parsing failures use the offline fallback.
            fallback = _catalog_fallback_for_ox_alpha()
            _ox_alpha_available = bool(fallback) if fallback is not None else False
        return _ox_alpha_available


def _patch_openrouter_catalog() -> None:
    """Keep the live OpenRouter picker catalog aware of Ox Alpha."""
    try:
        from hermes_cli import models as model_module
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[discord-model-paginator] model catalog import failed: %s", exc)
        return

    original = getattr(model_module, "fetch_openrouter_models", None)
    if original is None or getattr(original, "_nyxie_ox_alpha_wrapped", False):
        return

    @wraps(original)
    def _patched(*args, **kwargs):
        rows = list(original(*args, **kwargs) or [])
        if not _ox_alpha_is_available():
            return rows
        existing = {
            str(row[0])
            for row in rows
            if isinstance(row, (tuple, list)) and row
        }
        if OX_ALPHA_MODEL not in existing:
            rows.append((OX_ALPHA_MODEL, "free"))
        return rows

    _patched._nyxie_ox_alpha_wrapped = True
    _patched._nyxie_ox_alpha_original = original
    model_module.fetch_openrouter_models = _patched
    logger.info("[discord-model-paginator] OpenRouter picker will include %s.", OX_ALPHA_MODEL)


def _patch_live_discord_adapter() -> None:
    """Patch the adapter module materialized by Hermes' platform loader.

    Bundled platform plugins are imported under ``hermes_plugins.*`` while a
    direct import uses ``plugins.platforms.*``. Those are different module
    objects, so patching only the latter leaves the live gateway untouched.
    Materialize Discord during gateway startup, then match the adapter by its
    source path so this remains tolerant of namespace changes.
    """
    if os.getenv("_HERMES_GATEWAY", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    try:
        from gateway.platform_registry import platform_registry

        platform_registry.get("discord")
    except Exception as exc:  # pragma: no cover - Discord may be unconfigured
        logger.debug("[discord-model-paginator] live Discord materialization skipped: %s", exc)

    suffix = "/plugins/platforms/discord/adapter.py"
    for module in list(sys.modules.values()):
        if module is None:
            continue
        module_file = str(getattr(module, "__file__", "")).replace("\\", "/")
        if not module_file.endswith(suffix):
            continue
        try:
            if getattr(module, "DISCORD_AVAILABLE", False) and hasattr(module, "ModelPickerView"):
                _install_view(module)
                _wrap_define(module)
                logger.info(
                    "[discord-model-paginator] Patched live Discord adapter module %s.",
                    getattr(module, "__name__", "<unknown>"),
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[discord-model-paginator] live adapter patch failed: %s", exc)

def _patch() -> None:
    _patch_openrouter_catalog()

    # Import the live discord adapter module.  If Discord isn't wired up yet
    # (DISCORD_AVAILABLE False / view classes not defined), bail; the gateway
    # will lazily re-run _define_discord_view_classes later and we re-wrap it.
    try:
        from plugins.platforms.discord import adapter as ad
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[discord-model-paginator] adapter import failed: %s", exc)
        return

    if not getattr(ad, "DISCORD_AVAILABLE", False):
        logger.info("[discord-model-paginator] Discord not available yet; will patch on re-init.")
        _wrap_define(ad)
        return
    if not hasattr(ad, "ModelPickerView"):
        logger.info("[discord-model-paginator] ModelPickerView not defined yet; will patch on re-init.")
        _wrap_define(ad)
        return

    _install_view(ad)
    _wrap_define(ad)
    logger.info("[discord-model-paginator] Paginated Discord model picker installed.")
    _patch_live_discord_adapter()


def _wrap_define(ad) -> None:
    """Wrap _define_discord_view_classes so a lazy re-init re-applies our view."""
    original = getattr(ad, "_define_discord_view_classes", None)
    if original is None or getattr(original, "_nyxie_wrapped", False):
        return

    def _wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        try:
            if getattr(ad, "DISCORD_AVAILABLE", False) and hasattr(ad, "ModelPickerView"):
                _install_view(ad)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[discord-model-paginator] re-init patch failed: %s", exc)
        return result

    _wrapped._nyxie_wrapped = True
    ad._define_discord_view_classes = _wrapped


def _install_view(ad) -> None:
    discord = ad.discord
    _component_check_auth = ad._component_check_auth
    _truncate = ad._truncate_discord_component_text
    _LIMIT = ad._DISCORD_SELECT_FIELD_LIMIT
    logger_ref = getattr(ad, "logger", logger)

    class PaginatedModelPickerView(discord.ui.View):
        """Discord model-switch view with 25-per-page pagination.

        Identical two-step drill-down (provider -> model) and auth/expensive-
        confirm/timeout semantics to the bundled view, plus Prev/Next paging.
        """

        def __init__(
            self,
            providers,
            current_model,
            current_provider,
            session_key,
            on_model_selected,
            allowed_user_ids,
            allowed_role_ids=None,
        ):
            super().__init__(timeout=120)
            self.providers = providers
            self.current_model = current_model
            self.current_provider = current_provider
            self.session_key = session_key
            self.on_model_selected = on_model_selected
            self.allowed_user_ids = allowed_user_ids
            self.allowed_role_ids = allowed_role_ids or set()
            self.resolved = False
            self._selected_provider = ""
            self._pending_expensive_model = ""
            self._model_page = 0
            self._provider_page = 0
            self._provider_models = []
            self._build_provider_select()

        def _check_auth(self, interaction):
            return _component_check_auth(
                interaction, self.allowed_user_ids, self.allowed_role_ids
            )

        def _build_provider_select(self, page=0):
            self.clear_items()
            self._provider_page = max(0, page)
            total = len(self.providers)
            pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
            self._provider_page = min(self._provider_page, pages - 1)
            start = self._provider_page * PAGE_SIZE
            end = start + PAGE_SIZE
            page_providers = self.providers[start:end]

            options = []
            for p in page_providers:
                count = p.get("total_models", len(p.get("models", [])))
                label = f"{p['name']} ({count} models)"
                desc = "current" if p.get("is_current") else None
                options.append(
                    discord.SelectOption(
                        label=_truncate(label, _LIMIT),
                        value=p["slug"],
                        description=desc,
                    )
                )
            if not options:
                return

            placeholder = (
                f"Page {self._provider_page + 1}/{pages} — choose a provider..."
                if total > PAGE_SIZE
                else "Choose a provider..."
            )
            select = discord.ui.Select(
                placeholder=placeholder,
                options=options,
                custom_id="model_provider_select",
            )
            select.callback = self._on_provider_selected
            self.add_item(select)

            # Pagination row (only when there is more than one page)
            if pages > 1:
                prev_btn = discord.ui.Button(
                    label="\u25c0 Prev",
                    style=discord.ButtonStyle.grey,
                    custom_id="provider_page_prev",
                    disabled=(self._provider_page == 0),
                )
                prev_btn.callback = self._on_provider_page_prev
                self.add_item(prev_btn)

                next_btn = discord.ui.Button(
                    label="Next \u25b6",
                    style=discord.ButtonStyle.grey,
                    custom_id="provider_page_next",
                    disabled=(self._provider_page >= pages - 1),
                )
                next_btn.callback = self._on_provider_page_next
                self.add_item(next_btn)

            cancel_btn = discord.ui.Button(
                label="Cancel", style=discord.ButtonStyle.red, custom_id="model_cancel"
            )
            cancel_btn.callback = self._on_cancel
            self.add_item(cancel_btn)

        async def _on_provider_page_prev(self, interaction):
            if not self._check_auth(interaction):
                await interaction.response.send_message("You're not authorized~", ephemeral=True)
                return
            self._build_provider_select(page=self._provider_page - 1)
            await interaction.response.edit_message(view=self)

        async def _on_provider_page_next(self, interaction):
            if not self._check_auth(interaction):
                await interaction.response.send_message("You're not authorized~", ephemeral=True)
                return
            self._build_provider_select(page=self._provider_page + 1)
            await interaction.response.edit_message(view=self)

        def _build_model_select(self, provider_slug, page=0):
            self.clear_items()
            provider = next(
                (p for p in self.providers if p["slug"] == provider_slug), None
            )
            if not provider:
                return

            models = provider.get("models", [])
            self._provider_models = models
            total = len(models)
            pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
            self._model_page = max(0, min(page, pages - 1))
            start = self._model_page * PAGE_SIZE
            end = start + PAGE_SIZE
            page_models = models[start:end]

            options = []
            for model_id in page_models:
                short = model_id.split("/")[-1] if "/" in model_id else model_id
                options.append(
                    discord.SelectOption(
                        label=_truncate(short, _LIMIT),
                        value=_truncate(model_id, _LIMIT),
                    )
                )
            if not options:
                return

            select = discord.ui.Select(
                placeholder=f"Page {self._model_page + 1}/{pages} — choose a model...",
                options=options,
                custom_id="model_model_select",
            )
            select.callback = self._on_model_selected
            self.add_item(select)

            # Pagination row
            prev_btn = discord.ui.Button(
                label="\u25c0 Prev",
                style=discord.ButtonStyle.grey,
                custom_id="model_page_prev",
                disabled=(self._model_page == 0),
            )
            prev_btn.callback = self._on_page_prev
            self.add_item(prev_btn)

            next_btn = discord.ui.Button(
                label="Next \u25b6",
                style=discord.ButtonStyle.grey,
                custom_id="model_page_next",
                disabled=(self._model_page >= pages - 1),
            )
            next_btn.callback = self._on_page_next
            self.add_item(next_btn)

            back_btn = discord.ui.Button(
                label="\u25c0 Back", style=discord.ButtonStyle.grey, custom_id="model_back"
            )
            back_btn.callback = self._on_back
            self.add_item(back_btn)

            cancel_btn = discord.ui.Button(
                label="Cancel", style=discord.ButtonStyle.red, custom_id="model_cancel2"
            )
            cancel_btn.callback = self._on_cancel
            self.add_item(cancel_btn)

        def _build_expensive_confirm(self, model_id):
            self.clear_items()
            self._pending_expensive_model = model_id
            confirm_btn = discord.ui.Button(
                label="Switch anyway",
                style=discord.ButtonStyle.red,
                custom_id="model_expensive_confirm",
            )
            confirm_btn.callback = self._on_expensive_confirm
            self.add_item(confirm_btn)
            cancel_btn = discord.ui.Button(
                label="Cancel", style=discord.ButtonStyle.grey, custom_id="model_expensive_cancel"
            )
            cancel_btn.callback = self._on_cancel
            self.add_item(cancel_btn)

        async def _expensive_warning_for(self, model_id):
            try:
                from hermes_cli.model_selection_guards import combined_selection_warning

                return await asyncio.to_thread(
                    combined_selection_warning,
                    model_id,
                    provider=self._selected_provider,
                )
            except Exception:
                return None

        async def _on_provider_selected(self, interaction):
            if not self._check_auth(interaction):
                await interaction.response.send_message("You're not authorized~", ephemeral=True)
                return
            provider_slug = interaction.data["values"][0]
            self._selected_provider = provider_slug
            provider = next(
                (p for p in self.providers if p["slug"] == provider_slug), None
            )
            pname = provider.get("name", provider_slug) if provider else provider_slug
            self._build_model_select(provider_slug, page=0)
            total = len(provider.get("models", [])) if provider else 0
            pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
            note = ""
            if total > PAGE_SIZE:
                note = f"\n*Showing page 1/{pages} — use ◀ Prev / Next ▶ to reach all {total} models.*"
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="\u2699 Model Configuration",
                    description=f"Provider: **{pname}**\nSelect a model:{note}",
                    color=discord.Color.blue(),
                ),
                view=self,
            )

        async def _switch_selected_model(self, interaction, model_id):
            if self.resolved:
                await interaction.response.send_message("Already resolved~", ephemeral=True)
                return
            if not self._check_auth(interaction):
                await interaction.response.send_message("You're not authorized~", ephemeral=True)
                return
            self.resolved = True
            self.clear_items()
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="\u2699 Switching Model",
                    description=f"Switching to `{model_id}`...",
                    color=discord.Color.blue(),
                ),
                view=None,
            )
            try:
                result_text = await self.on_model_selected(
                    str(interaction.channel_id),
                    model_id,
                    self._selected_provider,
                )
            except Exception as exc:
                result_text = f"Error switching model: {exc}"
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="\u2699 Model Switched",
                    description=result_text,
                    color=discord.Color.green(),
                ),
                view=None,
            )

        async def _on_model_selected(self, interaction):
            if self.resolved:
                await interaction.response.send_message("Already resolved~", ephemeral=True)
                return
            if not self._check_auth(interaction):
                await interaction.response.send_message("You're not authorized~", ephemeral=True)
                return
            model_id = interaction.data["values"][0]
            warning = await self._expensive_warning_for(model_id)
            if warning is not None:
                self._build_expensive_confirm(model_id)
                await interaction.response.edit_message(
                    embed=discord.Embed(
                        title=f"\u26a0 {warning.title}",
                        description=warning.message,
                        color=discord.Color.red(),
                    ),
                    view=self,
                )
                return
            await self._switch_selected_model(interaction, model_id)

        async def _on_page_prev(self, interaction):
            if not self._check_auth(interaction):
                await interaction.response.send_message("You're not authorized~", ephemeral=True)
                return
            self._build_model_select(self._selected_provider, page=self._model_page - 1)
            await interaction.response.edit_message(view=self)

        async def _on_page_next(self, interaction):
            if not self._check_auth(interaction):
                await interaction.response.send_message("You're not authorized~", ephemeral=True)
                return
            self._build_model_select(self._selected_provider, page=self._model_page + 1)
            await interaction.response.edit_message(view=self)

        async def _on_expensive_confirm(self, interaction):
            if not self._check_auth(interaction):
                await interaction.response.send_message("You're not authorized~", ephemeral=True)
                return
            if not self._pending_expensive_model:
                await interaction.response.send_message("Model selection expired.", ephemeral=True)
                return
            await self._switch_selected_model(interaction, self._pending_expensive_model)

        async def _on_back(self, interaction):
            if not self._check_auth(interaction):
                await interaction.response.send_message("You're not authorized~", ephemeral=True)
                return
            self._build_provider_select()
            try:
                from hermes_cli.providers import get_label

                provider_label = get_label(self.current_provider)
            except Exception:
                provider_label = self.current_provider
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="\u2699 Model Configuration",
                    description=(
                        f"Current model: `{self.current_model or 'unknown'}`\n"
                        f"Provider: {provider_label}\n\n"
                        f"Select a provider:"
                    ),
                    color=discord.Color.blue(),
                ),
                view=self,
            )

        async def _on_cancel(self, interaction):
            self.resolved = True
            self.clear_items()
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="\u2699 Model Configuration",
                    description="Model selection cancelled.",
                    color=discord.Color.greyple(),
                ),
                view=self,
            )

        async def on_timeout(self):
            self.resolved = True
            self.clear_items()
            msg = getattr(self, "_message", None)
            if msg:
                try:
                    embed = discord.Embed(
                        title="\u2699 Model Configuration",
                        description="\u23f1 Selection expired — no model change.",
                        color=discord.Color.greyple(),
                    )
                    await msg.edit(embed=embed, view=self)
                except Exception:
                    pass

    # Install our view as the module global the adapter reads at call time.
    ad.ModelPickerView = PaginatedModelPickerView
    # Keep a back-reference so we don't lose the original if needed.
    ad._ModelPickerView_original = getattr(ad, "_ModelPickerView_original", None) or ad.ModelPickerView


def register(ctx):  # noqa: D401 - plugin entry point
    """Plugin entry point — called by the Hermes plugin system on gateway boot."""
    _patch()
