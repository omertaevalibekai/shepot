"""Распознавание речи: облако (OpenAI) или локально (faster-whisper)."""

from __future__ import annotations

import io
import threading

from .providers import ProviderError, openai_client


class SttError(RuntimeError):
    pass


# ---------------------------------------------------------------- OpenAI ---

def _transcribe_openai(wav: bytes, model: str, language: str | None, hint: str) -> str:
    try:
        client = openai_client()
    except ProviderError as exc:
        raise SttError(str(exc)) from exc
    audio = io.BytesIO(wav)
    audio.name = "speech.wav"  # SDK определяет формат по имени файла
    kwargs = {"model": model, "file": audio, "response_format": "text"}
    if language:
        kwargs["language"] = language
    if hint:
        kwargs["prompt"] = hint
    try:
        result = client.audio.transcriptions.create(**kwargs)
    except Exception as exc:
        raise SttError(f"Ошибка распознавания: {exc}") from exc
    # при response_format="text" SDK отдаёт строку
    return result if isinstance(result, str) else getattr(result, "text", "")


# --------------------------------------------------------- faster-whisper ---

_local_models: dict[tuple[str, str], object] = {}
_local_lock = threading.Lock()


def _get_local_model(name: str, device: str):
    key = (name, device)
    with _local_lock:
        if key not in _local_models:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise SttError(
                    "Не установлен faster-whisper. Выполните: "
                    "pip install faster-whisper"
                ) from exc
            if device == "auto":
                device = "cuda" if _cuda_available() else "cpu"
            compute_type = "float16" if device == "cuda" else "int8"
            try:
                _local_models[key] = WhisperModel(
                    name, device=device, compute_type=compute_type
                )
            except Exception as exc:
                raise SttError(f"Не удалось загрузить модель {name}: {exc}") from exc
        return _local_models[key]


def _cuda_available() -> bool:
    try:
        import ctypes

        ctypes.CDLL("nvcuda.dll")
        return True
    except OSError:
        return False


def _transcribe_local(wav: bytes, name: str, device: str,
                      language: str | None, hint: str) -> str:
    model = _get_local_model(name, device)
    try:
        segments, _info = model.transcribe(
            io.BytesIO(wav),
            language=language,
            vad_filter=True,
            initial_prompt=hint or None,
            beam_size=5,
        )
        return "".join(segment.text for segment in segments).strip()
    except Exception as exc:
        raise SttError(f"Ошибка локального распознавания: {exc}") from exc


# ------------------------------------------------------------------- API ---

def transcribe(wav: bytes, cfg) -> str:
    """Переводит WAV-байты в текст согласно настройкам."""
    language = cfg.get("source_language") or "auto"
    language = None if language == "auto" else language
    vocabulary = [t for t in (cfg.get("vocabulary") or []) if t.strip()]
    hint = ", ".join(vocabulary)

    if cfg.get("stt_backend") == "local":
        return _transcribe_local(
            wav, cfg.get("local_model"), cfg.get("local_device"), language, hint
        )
    return _transcribe_openai(wav, cfg.get("stt_model"), language, hint)


def preload(cfg) -> None:
    """Прогревает локальную модель, чтобы первая диктовка не ждала загрузки."""
    if cfg.get("stt_backend") == "local":
        try:
            _get_local_model(cfg.get("local_model"), cfg.get("local_device"))
        except SttError:
            pass
