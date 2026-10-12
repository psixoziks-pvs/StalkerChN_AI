"""TTS-обёртки для голосовых ответов NPC (опционально).

Бэкенды: kokoro (Kokoro-82M, ru), piper (легковесный), edge (Microsoft Edge TTS,
нужен интернет), espeak (fallback). Результат — аудиофайл в tts_cache/, путь
возвращается серверу; игра проигрывает его через EAX/3D-звук X-Ray (см. Lua-мод).
"""
from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path

from config_loader import Config, ROOT

log = logging.getLogger("scnai.tts")


class TtsEngine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cache = ROOT / cfg.tts.cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)
        self._engine = None
        if cfg.tts.enabled:
            log.info("TTS включён, бэкенд: %s", cfg.tts.backend)

    # ---------- публичный API ----------
    async def synthesize(self, text: str) -> str | None:
        """Вернуть абсолютный путь к ogg/wav или None, если TTS выключен."""
        if not self.cfg.tts.enabled or not text.strip():
            return None
        key = hashlib.md5(
            f"{self.cfg.tts.backend}|{self.cfg.tts.voice}|{text}".encode()).hexdigest()
        ext = self.cfg.tts.output_format
        out = self.cache / f"npc_{key}.{ext}"
        if out.exists():
            return str(out)
        try:
            b = self.cfg.tts.backend.lower()
            if b == "kokoro":
                self._kokoro(text, out)
            elif b == "piper":
                self._piper(text, out)
            elif b == "edge":
                await self._edge(text, out)
            elif b == "espeak":
                self._espeak(text, out)
            else:
                log.warning("Неизвестный TTS-бэкенд %s", b)
                return None
            return str(out) if out.exists() else None
        except Exception as e:  # noqa: BLE001 — TTS не должен ронять сервер
            log.error("TTS ошибка (%s): %s", self.cfg.tts.backend, e)
            return None

    # ---------- реализации ----------
    def _ensure_kokoro(self):
        if self._engine is None:
            from kokoro import KPipeline  # pip install kokoro
            self._engine = KPipeline(lang_code="ru")
        return self._engine

    def _kokoro(self, text: str, out: Path):
        pipe = self._ensure_kokoro()
        for _, _, audio in pipe(text, voice=self.cfg.tts.voice):
            import soundfile as sf
            sf.write(out.with_suffix(".wav"), audio, 24000)
            break
        self._maybe_convert(out)

    def _piper(self, text: str, out: Path):
        model = str(ROOT / "models" / f"piper-{self.cfg.tts.voice}.onnx")
        wav = out.with_suffix(".wav")
        p = subprocess.run(
            ["piper", "--model", model, "--output_file", str(wav),
             "--length_scale", str(1.0 / max(0.5, self.cfg.tts.speed))],
            input=text.encode(), capture_output=True)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.decode(errors="ignore")[:300])
        self._maybe_convert(out)

    async def _edge(self, text: str, out: Path):
        import edge_tts  # pip install edge-tts
        mp3 = out.with_suffix(".mp3")
        comm = edge_tts.Communicate(text, voice=self.cfg.tts.voice or "ru-RU-DmitryNeural")
        await comm.save(str(mp3))
        if out.suffix == ".ogg":
            subprocess.run(["ffmpeg", "-y", "-i", str(mp3), "-c:a", "libvorbis", str(out)],
                           check=False, capture_output=True)
            mp3.unlink(missing_ok=True)
        else:
            mp3.replace(out)

    def _espeak(self, text: str, out: Path):
        wav = out.with_suffix(".wav")
        subprocess.run(["espeak-ng", "-v", f"ru/{self.cfg.tts.voice}", "-s", "150",
                        "-w", str(wav), text], check=True, capture_output=True)
        self._maybe_convert(out)

    def _maybe_convert(self, out: Path):
        """Конвертировать промежуточный .wav в целевой формат (ogg для X-Ray)."""
        wav = out.with_suffix(".wav")
        if out.suffix == ".ogg" and wav.exists():
            subprocess.run(["ffmpeg", "-y", "-i", str(wav), "-c:a", "libvorbis", str(out)],
                           check=False, capture_output=True)
            wav.unlink(missing_ok=True)
