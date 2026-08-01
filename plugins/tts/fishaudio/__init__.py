"""Fish Audio TTS provider for Hermes.

This plugin deliberately uses EpicIsTheOne/fish-audio-tts-toolkit as the
provider boundary instead of duplicating Fish Audio API and tagging logic in
Hermes. The toolkit runs as a small localhost helper and this adapter speaks
its ``POST /api/tts/audio`` contract.

The helper is started lazily on the first synthesis request when
``tts.fishaudio.auto_start`` is enabled (the default) and the bundled toolkit
checkout has its npm dependencies installed. Status checks never start a
process or make a network request.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agent.tts_provider import TTSProvider

logger = logging.getLogger(__name__)

_DEFAULT_HELPER_URL = "http://127.0.0.1:3027"
_DEFAULT_MODEL = "s2.1-pro"
_DEFAULT_LATENCY = "low"
_DEFAULT_TIMEOUT = 120.0
_MAX_TOOLKIT_TEXT = 2500
_SUPPORTED_FORMATS = frozenset({"mp3", "wav", "opus", "pcm"})
_FORMAT_EXTENSIONS = {
    "mp3": ".mp3",
    "wav": ".wav",
    "opus": ".opus",
 "pcm": ".pcm",
}


class FishAudioTTSProvider(TTSProvider):
    """Hermes adapter for the local Fish Audio toolkit helper."""

    _process: Optional[subprocess.Popen] = None
    _process_lock = threading.Lock()

    @property
    def name(self) -> str:
        return "fishaudio"

    @property
    def display_name(self) -> str:
        return "Fish Audio"

    @staticmethod
    def _config() -> Dict[str, Any]:
        """Load provider-specific config without ever reading secret values."""
        try:
            from hermes_cli.config import load_config

            tts = load_config().get("tts") or {}
        except Exception:
            return {}
        if not isinstance(tts, dict):
            return {}

        merged: Dict[str, Any] = {}
        legacy = tts.get("fishaudio")
        if isinstance(legacy, dict):
            merged.update(legacy)
        providers = tts.get("providers")
        if isinstance(providers, dict) and isinstance(providers.get("fishaudio"), dict):
            # The explicit provider namespace wins over the legacy-style block.
            merged.update(providers["fishaudio"])
        return merged

    @classmethod
    def _toolkit_dir(cls, config: Optional[Dict[str, Any]] = None) -> Path:
        config = config if config is not None else cls._config()
        raw = config.get("toolkit_dir")
        if raw:
            path = Path(os.path.expandvars(str(raw))).expanduser()
            if not path.is_absolute():
                path = Path(__file__).resolve().parent / path
            return path.resolve()
        # Keep the checkout beside the namespaced plugin directory.  This is
        # friendlier to Windows/MSYS path handling and keeps the plugin folder
        # itself limited to Hermes metadata/code.
        return (Path(__file__).resolve().parents[2] / "fishaudio-toolkit").resolve()

    @classmethod
    def _helper_url(cls, config: Optional[Dict[str, Any]] = None) -> str:
        config = config if config is not None else cls._config()
        return str(config.get("helper_url") or _DEFAULT_HELPER_URL).rstrip("/")

    @classmethod
    def _timeout(cls, config: Optional[Dict[str, Any]] = None) -> float:
        config = config if config is not None else cls._config()
        try:
            return max(1.0, min(float(config.get("timeout_seconds", _DEFAULT_TIMEOUT)), 600.0))
        except (TypeError, ValueError):
            return _DEFAULT_TIMEOUT

    @classmethod
    def _helper_key(cls) -> str:
        # This is an optional localhost-helper credential, not the Fish API
        # credential. Never put either secret in config.yaml.
        return (
            os.environ.get("FISH_HELPER_API_KEY", "").strip()
            or os.environ.get("FISH_AUDIO_HELPER_API_KEY", "").strip()
        )

    @classmethod
    def _request(
        cls,
        method: str,
        url: str,
        *,
        body: Optional[bytes] = None,
        timeout: Optional[float] = None,
    ) -> Tuple[int, Dict[str, str], bytes]:
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        helper_key = cls._helper_key()
        if helper_key:
            headers["Authorization"] = f"Bearer {helper_key}"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout or _DEFAULT_TIMEOUT) as response:
                return response.status, dict(response.headers.items()), response.read()
        except HTTPError as exc:
            detail = exc.read()
            return exc.code, dict(exc.headers.items()), detail

    @classmethod
    def _health(cls, config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Return helper health, or None when localhost is not running."""
        try:
            status, _headers, raw = cls._request(
                "GET",
                f"{cls._helper_url(config)}/healthz",
                timeout=0.75,
            )
        except (OSError, URLError):
            return None
        if status != 200:
            return None
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    @classmethod
    def _node_command(cls) -> Optional[str]:
        return shutil.which("node.exe") or shutil.which("node")

    @classmethod
    def _npm_command(cls) -> Optional[str]:
        return shutil.which("npm.cmd") or shutil.which("npm")

    @classmethod
    def _can_auto_start(cls, config: Optional[Dict[str, Any]] = None) -> bool:
        config = config if config is not None else cls._config()
        toolkit = cls._toolkit_dir(config)
        return bool(
            config.get("auto_start", True)
            and toolkit.is_dir()
            and (toolkit / "package.json").is_file()
            and (toolkit / "node_modules").is_dir()
            and cls._node_command()
            and cls._npm_command()
        )

    def is_available(self) -> bool:
        """Return readiness without launching the helper or making network calls."""
        config = self._config()
        health = self._health(config)
        if health is not None:
            return health.get("ok") is True and health.get("fishConfigured") is not False
        # A lazy-startable checkout is considered ready. The first actual TTS
        # call performs the real health/API check and gives a useful error.
        return self._can_auto_start(config) and bool(
            os.environ.get("FISH_AUDIO_API_KEY", "").strip()
            or (self._toolkit_dir(config) / ".env").is_file()
        )

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {"id": "s2.1-pro", "display": "S2.1 Pro", "max_text_length": _MAX_TOOLKIT_TEXT},
            {"id": "s2.1-pro-free", "display": "S2.1 Pro Free", "max_text_length": _MAX_TOOLKIT_TEXT},
            {"id": "s2-pro", "display": "S2 Pro", "max_text_length": _MAX_TOOLKIT_TEXT},
            {"id": "s1", "display": "S1", "max_text_length": _MAX_TOOLKIT_TEXT},
        ]

    def default_model(self) -> Optional[str]:
        return str(self._config().get("model") or _DEFAULT_MODEL)

    def list_voices(self) -> List[Dict[str, Any]]:
        config = self._config()
        voice = str(config.get("voice") or config.get("voice_id") or config.get("reference_id") or "").strip()
        if not voice:
            return []
        return [{"id": voice, "display": f"Configured Fish voice ({voice})", "language": "auto"}]

    def default_voice(self) -> Optional[str]:
        voices = self.list_voices()
        return str(voices[0]["id"]) if voices else None

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Fish Audio",
            "badge": "paid",
            "tag": "Toolkit-backed TTS with automatic emotion tagging and local wake-mode playback",
            "env_vars": [
                {
                    "key": "FISH_AUDIO_API_KEY",
                    "prompt": "Fish Audio API key",
                    "url": "https://fish.audio/app/api-keys",
                },
            ],
        }

    @property
    def voice_compatible(self) -> bool:
        return True

    @classmethod
    def _start_helper(cls, config: Dict[str, Any]) -> None:
        """Start the toolkit helper once and wait until its health endpoint responds."""
        with cls._process_lock:
            health = cls._health(config)
            if health is not None:
                if health.get("fishConfigured") is False:
                    raise RuntimeError(
                        "Fish Audio toolkit is running but has no FISH_AUDIO_API_KEY. "
                        "Set it in the toolkit .env or Hermes .env and restart the helper."
                    )
                return

            if not cls._can_auto_start(config):
                toolkit = cls._toolkit_dir(config)
                raise RuntimeError(
                    "Fish Audio helper is not running and cannot be auto-started. "
                    f"Install the toolkit dependencies in {toolkit} with `npm install`, "
                    "or set tts.fishaudio.auto_start: false and run `npm start` there."
                )

            npm = cls._npm_command()
            toolkit = cls._toolkit_dir(config)
            env = os.environ.copy()
            model = str(config.get("model") or "").strip()
            if model:
                env["FISH_TTS_BACKEND"] = model
            base_url = str(config.get("fish_base_url") or "").strip()
            if base_url:
                env["FISH_AUDIO_BASE_URL"] = base_url

            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                cls._process = subprocess.Popen(
                    [npm, "start"],
                    cwd=str(toolkit),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
            except OSError as exc:
                raise RuntimeError(f"Could not start Fish Audio toolkit helper: {exc}") from exc

            deadline = time.monotonic() + min(cls._timeout(config), 30.0)
            while time.monotonic() < deadline:
                health = cls._health(config)
                if health is not None:
                    if health.get("fishConfigured") is False:
                        raise RuntimeError(
                            "Fish Audio toolkit started, but FISH_AUDIO_API_KEY is missing. "
                            "Set it in Hermes .env or the toolkit .env."
                        )
                    return
                if cls._process.poll() is not None:
                    cls._process = None
                    raise RuntimeError(
                        "Fish Audio toolkit exited before its helper API became ready. "
                        f"Check npm start in {toolkit}."
                    )
                time.sleep(0.25)
            raise RuntimeError(
                f"Timed out waiting for Fish Audio toolkit helper at {cls._helper_url(config)}."
            )

    @classmethod
    def _resolve_output(cls, output_path: str, fmt: str) -> Tuple[Path, str]:
        requested = str(fmt or "mp3").strip().lower().lstrip(".")
        if requested == "ogg":
            requested = "opus"
        if requested == "raw":
            requested = "pcm"
        if requested not in _SUPPORTED_FORMATS:
            requested = "mp3"
        target = Path(output_path)
        expected_suffix = _FORMAT_EXTENSIONS[requested]
        if target.suffix.lower() != expected_suffix:
            target = target.with_suffix(expected_suffix)
        return target, requested

    def synthesize(
        self,
        text: str,
        output_path: str,
        *,
        voice: Optional[str] = None,
        model: Optional[str] = None,
        speed: Optional[float] = None,
        format: str = "mp3",
        **extra: Any,
    ) -> str:
        config = self._config()
        text = str(text or "").strip()
        if not text:
            raise ValueError("Fish Audio TTS requires non-empty text")
        max_length = int(config.get("max_text_length") or _MAX_TOOLKIT_TEXT)
        max_length = max(1, min(max_length, _MAX_TOOLKIT_TEXT))
        text = text[:max_length]

        target, resolved_format = self._resolve_output(output_path, format)
        target.parent.mkdir(parents=True, exist_ok=True)
        selected_voice = (
            voice
            or config.get("voice")
            or config.get("voice_id")
            or config.get("reference_id")
            or ""
        )
        selected_voice = str(selected_voice).strip()
        payload: Dict[str, Any] = {
            "text": text,
            "format": resolved_format,
            "latency": str(config.get("latency") or _DEFAULT_LATENCY),
            "includeAsteriskNarration": bool(config.get("include_asterisk_narration", False)),
        }
        if selected_voice:
            payload["voiceId"] = selected_voice

        # The toolkit owns emotion tagging, narration cleanup, and the Fish API
        # request. Hermes only handles lifecycle, transport, and file safety.
        self._start_helper(config)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            status, headers, raw = self._request(
                "POST",
                f"{self._helper_url(config)}/api/tts/audio",
                body=body,
                timeout=self._timeout(config),
            )
        except (OSError, URLError) as exc:
            raise RuntimeError(f"Fish Audio toolkit request failed: {exc}") from exc

        if status < 200 or status >= 300:
            detail = raw.decode("utf-8", errors="replace").strip()
            try:
                parsed = json.loads(detail)
                error = str(parsed.get("error") or "").strip()
                nested_detail = str(parsed.get("detail") or "").strip()
                if error and nested_detail:
                    detail = f"{error}: {nested_detail}"
                elif error or nested_detail:
                    detail = error or nested_detail
            except json.JSONDecodeError:
                pass
            raise RuntimeError(f"Fish Audio toolkit returned HTTP {status}: {detail[:500]}")
        if not raw:
            raise RuntimeError("Fish Audio toolkit returned empty audio")

        temp_path = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        try:
            temp_path.write_bytes(raw)
            os.replace(temp_path, target)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
        logger.info("Fish Audio synthesized %d bytes via toolkit: %s", len(raw), target)
        return str(target)


def register(ctx) -> None:
    """Register the toolkit-backed Fish Audio provider with Hermes."""
    ctx.register_tts_provider(FishAudioTTSProvider())


__all__ = ["FishAudioTTSProvider", "register"]