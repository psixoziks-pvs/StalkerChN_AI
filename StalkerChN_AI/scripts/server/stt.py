"""Speech-to-Text: стриминг микрофона -> текст реплики игрока.

Бэкенды: faster-whisper (локально, рекомендуется), vosk (лёгкий), openai (облако).
Интерфейс единый:recognize(pcm_bytes) -> str.
"""
from __future__ import annotations

import io
import logging
import wave
from typing import Protocol

import numpy as np

from config_loader import Config

log = logging.getLogger("scnai.stt")


class SttBackend(Protocol):
    def recognize(self, pcm: bytes, sample_rate: int) -> str: ...


def _pcm_to_f32(pcm: bytes, encoding: str) -> np.ndarray:
    if encoding == "pcm_f32le":
        return np.frombuffer(pcm, dtype=np.float32)
    if encoding == "pcm_s16le":
        return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    raise ValueError(f"Неизвестное кодирование: {encoding}")


def _to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    pcm16 = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16.tobytes())
    return buf.getvalue()


class FasterWhisperStt:
    """faster-whisper: локальный, быстрый, хорошо понимает русский."""

    def __init__(self, cfg: Config):
        from faster_whisper import WhisperModel  # отложенный импорт
        self.sr = cfg.audio.sample_rate
        log.info("Загрузка faster-whisper '%s' (%s/%s)...",
                 cfg.stt.model, cfg.stt.device, cfg.stt.compute_type)
        self.model = WhisperModel(cfg.stt.model, device=cfg.stt.device,
                                  compute_type=cfg.stt.compute_type)
        self.language = cfg.stt.language or None

    def recognize(self, pcm: bytes, sample_rate: int) -> str:
        raise NotImplementedError

    def recognize_f32(self, audio: np.ndarray, sample_rate: int) -> str:
        segments, _ = self.model.transcribe(
            audio, language=self.language, beam_size=1, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


class VoskStt:
    """Vosk: очень лёгкий офлайн-движок с хорошей русской моделью."""

    def __init__(self, cfg: Config):
        from vosk import KaldiRecognizer, Model
        self._Rec = KaldiRecognizer
        self.model = Model(cfg.stt.vosk_model_path)
        self.sr = cfg.audio.sample_rate

    def recognize(self, pcm: bytes, sample_rate: int) -> str:
        rec = self._Rec(self.model, sample_rate)
        rec.AcceptWaveform(pcm)
        import json
        return json.loads(rec.FinalResult()).get("text", "").strip()


class OpenAIStt:
    """Облачный Whisper через OpenAI-совместимый API."""

    def __init__(self, cfg: Config):
        from openai import OpenAI
        self.client = OpenAI(base_url=cfg.llm.openai_base_url or None,
                             api_key=cfg.llm.openai_api_key or "none")
        self.model_name = cfg.stt.openai_stt_model
        self.sr = cfg.audio.sample_rate

    def recognize(self, pcm: bytes, sample_rate: int) -> str:
        audio = _resample_if_needed(pcm, sample_rate, self.sr)
        wav = _to_wav_bytes(audio, self.sr)
        res = self.client.audio.transcriptions.create(
            model=self.model_name, file=("audio.wav", io.BytesIO(wav)),
            language="ru")
        return (res.text or "").strip()


def _resample_if_needed(pcm, src_sr: int, dst_sr: int) -> np.ndarray:
    x = pcm if isinstance(pcm, np.ndarray) else np.frombuffer(pcm, dtype=np.float32)
    if src_sr == dst_sr:
        return x
    n = int(len(x) * dst_sr / src_sr)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def make_stt(cfg: Config):
    """Фабрика бэкендов. Возвращает объект с методом recognize_pcm(bytes)->str."""
    backend = cfg.stt.backend.lower()
    if backend == "faster-whisper":
        fw = FasterWhisperStt(cfg)

        def recognize_pcm(pcm: bytes) -> str:
            sr = cfg.audio.sample_rate
            if cfg.audio.encoding in ("pcm_f32le", "pcm_s16le"):
                x = _pcm_to_f32(pcm, cfg.audio.encoding)
                if cfg.audio.encoding == "pcm_s16le":
                    pass  # уже нормализовано в float
                return fw.recognize_f32(x, sr)
            # wav/webm из клиента — отдаём как есть через ffmpeg не делаем,
            # просим клиента присылать PCM (см. README).
            raise ValueError("Для faster-whisper клиент должен слать PCM")
        recognize_pcm.kind = backend
        return recognize_pcm

    if backend == "vosk":
        v = VoskStt(cfg)

        def recognize_pcm(pcm: bytes) -> str:
            # vosk хочет s16le
            x = _pcm_to_f32(pcm, cfg.audio.encoding)
            s16 = (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()
            return v.recognize(s16, cfg.audio.sample_rate)
        recognize_pcm.kind = backend
        return recognize_pcm

    if backend == "openai":
        o = OpenAIStt(cfg)

        def recognize_pcm(pcm: bytes) -> str:
            x = _pcm_to_f32(pcm, cfg.audio.encoding).tobytes()
            return o.recognize(x, cfg.audio.sample_rate)
        recognize_pcm.kind = backend
        return recognize_pcm

    raise ValueError(f"Неизвестный STT-бэкенд: {backend}")
