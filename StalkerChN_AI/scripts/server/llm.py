"""LLM-бэкенды для StalkerChN_AI: ollama | openai | anthropic | mock.

Каждый бэкенд реализует async generate(messages, temperature, max_tokens) -> str
и (для оценки отношения) async classify_sentiment(text) -> dict.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Protocol

import httpx

from config_loader import Config

log = logging.getLogger("scnai.llm")


class LlmBackend(Protocol):
    async def generate(self, messages: list[dict], temperature: float,
                       max_tokens: int) -> str: ...


# ---------- JSON-ответ модели (реплика + настроение + память) ----------
RESPONSE_SCHEMA_HINT = """Ответь СТРОГО одним JSON-объектом без пояснений:
{"say": "<реплика NPC вслух, 1-3 предложения, на русском>", \
"mood_delta": <целое -20..20: как слова игрока изменили твоё расположение>, \
"action": "none|neutral|friendly|offended|aggressive|alert_faction|tell_companions", \
"remember": "<короткий факт об игроке, если надо запомнить; иначе пустая строка>"}"""


def parse_npc_json(raw: str) -> dict:
    """Вытащить JSON из ответа модели (терпимо к ```json и мусору вокруг)."""
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {"say": raw.strip()[:400], "mood_delta": 0, "action": "none", "remember": ""}
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        # частая проблема LLM — неэкранированные кавычки в say; грубая чистка
        cleaned = m.group(0).replace("\n", "\\n")
        try:
            obj = json.loads(cleaned)
        except json.JSONDecodeError:
            return {"say": raw.strip()[:400], "mood_delta": 0,
                    "action": "none", "remember": ""}
    say = str(obj.get("say", "")).strip() or "..."
    try:
        delta = max(-20, min(20, int(obj.get("mood_delta", 0))))
    except (TypeError, ValueError):
        delta = 0
    action = str(obj.get("action", "none")).strip().lower()
    allowed = {"none", "neutral", "friendly", "offended", "aggressive",
               "alert_faction", "tell_companions"}
    if action not in allowed:
        action = "none"
    remember = str(obj.get("remember", "")).strip()[:200]
    return {"say": say[:600], "mood_delta": delta, "action": action, "remember": remember}


class OllamaLlm:
    def __init__(self, cfg: Config):
        self.url = cfg.llm.ollama_url.rstrip("/")
        self.model = cfg.llm.ollama_model

    async def generate(self, messages, temperature, max_tokens) -> str:
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "options": {"temperature": temperature, "num_predict": max_tokens},
                   "format": "json"}
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(f"{self.url}/api/chat", json=payload)
            r.raise_for_status()
            return r.json().get("message", {}).get("content", "")


class OpenAiLlm:
    def __init__(self, cfg: Config):
        self.base = cfg.llm.openai_base_url.rstrip("/")
        self.key = cfg.llm.openai_api_key
        self.model = cfg.llm.openai_model

    async def generate(self, messages, temperature, max_tokens) -> str:
        payload = {"model": self.model, "messages": messages,
                   "temperature": temperature, "max_tokens": max_tokens,
                   "response_format": {"type": "json_object"}}
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(f"{self.base}/chat/completions", json=payload,
                             headers={"Authorization": f"Bearer {self.key}"})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]


class AnthropicLlm:
    def __init__(self, cfg: Config):
        self.key = cfg.llm.anthropic_api_key
        self.model = cfg.llm.anthropic_model

    async def generate(self, messages, temperature, max_tokens) -> str:
        sys = next((m["content"] for m in messages if m["role"] == "system"), "")
        rest = [m for m in messages if m["role"] != "system"]
        payload = {"model": self.model, "max_tokens": max_tokens,
                   "temperature": temperature, "system": sys, "messages": rest}
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post("https://api.anthropic.com/v1/messages", json=payload,
                             headers={"x-api-key": self.key,
                                      "anthropic-version": "2023-06-01"})
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json().get("content", []))


class MockLlm:
    """Для отладки без GPU/интернета: детерминированные реплики по ключевым словам."""
    RULES = [
        (r"[\w\-]*(?:бля|еб[а-яё]|ел[а-яё]н|суч[а-яё]|урод|твар|мразь|идиот|дебил|долбан|вонюч|бандюг)[\w\-]*",
         {"say": "Ты чё несёшь?! Ещё раз такое услышу — ножки твоей не будет.",
          "mood_delta": -12, "action": "offended", "remember": "Оскорбил меня"}),
        (r"(привет|здаров|здорово|хай)",
         {"say": "Здорово, братан. Чё хотел-то?", "mood_delta": 2, "action": "friendly", "remember": ""}),
        (r"(долг|долговцы)",
         {"say": "Долг — порядок в Зоне. За такие слова здесь благодарят.", "mood_delta": 5,
          "action": "friendly", "remember": "Уважительно отозвался о Долге"}),
        (r"(спасибо|благодар)",
         {"say": "Да не за что. Главное — лишний раз в аномалии не светись.",
          "mood_delta": 3, "action": "friendly", "remember": ""}),
        (r"(выброс|бомба|радар)",
         {"say": "Выброс قريب. Укроиться бы, а там виднее станет.",
          "mood_delta": 0, "action": "neutral", "remember": "Спрашивал про выброс"}),
        (r"(помощь|помоги|нужна помощь)",
         {"say": "Чём могу — тем помогу. Говори, что стряслось.",
          "mood_delta": 4, "action": "friendly", "remember": "Обращался за помощью"}),
    ]

    async def generate(self, messages, temperature, max_tokens) -> str:
        last_user = next((m["content"] for m in reversed(messages)
                          if m["role"] == "user"), "")
        low = last_user.lower()
        for pat, out in self.RULES:
            if re.search(pat, low):
                return json.dumps(out, ensure_ascii=False)
        return json.dumps({"say": "Ну, ты это... в Зоне базар крепче держи.",
                           "mood_delta": 0, "action": "neutral", "remember": ""},
                          ensure_ascii=False)


def make_llm(cfg: Config) -> LlmBackend:
    b = cfg.llm.backend.lower()
    if b == "ollama":
        return OllamaLlm(cfg)
    if b == "openai":
        return OpenAiLlm(cfg)
    if b == "anthropic":
        return AnthropicLlm(cfg)
    if b == "mock":
        return MockLlm()
    raise ValueError(f"Неизвестный LLM-бэкенд: {b}")
