"""Загрузка конфигурации StalkerChN_AI из config/settings.ini."""
from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.environ.get("SCNAI_CONFIG", ROOT / "config" / "settings.ini"))


def _get(cp: configparser.ConfigParser, sec: str, key: str, fallback=None):
    try:
        return cp[sec][key].strip()
    except KeyError:
        return fallback


@dataclass
class ServerCfg:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass
class AudioCfg:
    sample_rate: int = 16000
    channels: int = 1
    encoding: str = "pcm_f32le"
    vox_threshold: float = 0.012
    min_utterance_sec: float = 0.7
    max_utterance_sec: float = 30.0


@dataclass
class SttCfg:
    backend: str = "faster-whisper"
    language: str = "ru"
    model: str = "small"
    device: str = "cpu"
    compute_type: str = "int8"
    vosk_model_path: str = "models/vosk-ru-0.42"
    openai_stt_model: str = "whisper-1"


@dataclass
class LlmCfg:
    backend: str = "ollama"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_api_key: str = ""
    anthropic_model: str = "claude-3-5-haiku-latest"
    anthropic_api_key: str = ""
    temperature: float = 0.9
    max_tokens: int = 220
    safety_prompt: bool = True


@dataclass
class NpcCfg:
    history_turns: int = 12
    fact_memory_size: int = 30
    hearing_decay_min: int = 60


@dataclass
class ReactionCfg:
    offended_threshold: int = -25
    friendly_threshold: int = 35
    aggressive_threshold: int = -55
    allow_faction_alert: bool = True
    faction_alert_radius: float = 25.0
    reaction_cooldown: float = 8.0


@dataclass
class TtsCfg:
    enabled: bool = False
    backend: str = "kokoro"
    voice: str = "ru"
    speed: float = 1.0
    output_format: str = "ogg"
    cache_dir: str = "tts_cache"


@dataclass
class Config:
    server: ServerCfg = field(default_factory=ServerCfg)
    audio: AudioCfg = field(default_factory=AudioCfg)
    stt: SttCfg = field(default_factory=SttCfg)
    llm: LlmCfg = field(default_factory=LlmCfg)
    npc: NpcCfg = field(default_factory=NpcCfg)
    reaction: ReactionCfg = field(default_factory=ReactionCfg)
    tts: TtsCfg = field(default_factory=TtsCfg)
    factions_allowed: list[str] = field(default_factory=list)
    log_level: str = "INFO"
    log_file: str = "logs/stalkernn.log"
    log_transcripts: bool = True


def load_config(path: Path | None = None) -> Config:
    p = path or CONFIG_PATH
    cp = configparser.ConfigParser(inline_comment_prefixes=(";",))
    if p.exists():
        cp.read(p, encoding="utf-8")

    cfg = Config()
    g = lambda s, k, d=None: _get(cp, s, k, d)

    cfg.server.host = g("server", "host", cfg.server.host)
    cfg.server.port = int(g("server", "port", cfg.server.port))

    a = cfg.audio
    a.sample_rate = int(g("audio", "sample_rate", a.sample_rate))
    a.channels = int(g("audio", "channels", a.channels))
    a.encoding = g("audio", "encoding", a.encoding)
    a.vox_threshold = float(g("audio", "vox_threshold", a.vox_threshold))
    a.min_utterance_sec = float(g("audio", "min_utterance_sec", a.min_utterance_sec))
    a.max_utterance_sec = float(g("audio", "max_utterance_sec", a.max_utterance_sec))

    s = cfg.stt
    s.backend = g("stt", "backend", s.backend)
    s.language = g("stt", "language", s.language)
    s.model = g("stt", "model", s.model)
    s.device = g("stt", "device", s.device)
    s.compute_type = g("stt", "compute_type", s.compute_type)
    s.vosk_model_path = g("stt", "vosk_model_path", s.vosk_model_path)
    s.openai_stt_model = g("stt", "openai_stt_model", s.openai_stt_model)

    l = cfg.llm
    l.backend = g("llm", "backend", l.backend)
    l.ollama_url = g("llm", "ollama_url", l.ollama_url)
    l.ollama_model = g("llm", "ollama_model", l.ollama_model)
    l.openai_base_url = g("llm", "openai_base_url", l.openai_base_url)
    l.openai_model = g("llm", "openai_model", l.openai_model)
    l.openai_api_key = g("llm", "openai_api_key", "") or os.environ.get("OPENAI_API_KEY", "")
    l.anthropic_model = g("llm", "anthropic_model", l.anthropic_model)
    l.anthropic_api_key = g("llm", "anthropic_api_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    l.temperature = float(g("llm", "temperature", l.temperature))
    l.max_tokens = int(g("llm", "max_tokens", l.max_tokens))
    l.safety_prompt = g("llm", "safety_prompt", "on").lower() in ("on", "true", "1", "yes")

    n = cfg.npc
    n.history_turns = int(g("npc", "history_turns", n.history_turns))
    n.fact_memory_size = int(g("npc", "fact_memory_size", n.fact_memory_size))
    n.hearing_decay_min = int(g("npc", "hearing_decay_min", n.hearing_decay_min))

    r = cfg.reaction
    r.offended_threshold = int(g("reaction", "offended_threshold", r.offended_threshold))
    r.friendly_threshold = int(g("reaction", "friendly_threshold", r.friendly_threshold))
    r.aggressive_threshold = int(g("reaction", "aggressive_threshold", r.aggressive_threshold))
    r.allow_faction_alert = g("reaction", "allow_faction_alert", "true").lower() in ("true", "1", "yes")
    r.faction_alert_radius = float(g("reaction", "faction_alert_radius", r.faction_alert_radius))
    r.reaction_cooldown = float(g("reaction", "reaction_cooldown", r.reaction_cooldown))

    t = cfg.tts
    t.enabled = g("tts", "enabled", "false").lower() in ("true", "1", "yes", "on")
    t.backend = g("tts", "backend", t.backend)
    t.voice = g("tts", "voice", t.voice)
    t.speed = float(g("tts", "speed", t.speed))
    t.output_format = g("tts", "output_format", t.output_format)
    t.cache_dir = g("tts", "cache_dir", t.cache_dir)

    allowed = g("factions", "allowed", "") or ""
    cfg.factions_allowed = [x.strip().lower() for x in allowed.split(",") if x.strip()]

    cfg.log_level = g("logging", "level", cfg.log_level)
    cfg.log_file = g("logging", "file", cfg.log_file)
    cfg.log_transcripts = g("logging", "log_transcripts", "true").lower() in ("true", "1", "yes")
    return cfg
