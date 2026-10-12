"""StalkerChN_AI — голосовой AI-мод для S.T.A.L.K.E.R. Чистое Небо / Зов Припяти.

Сервер: FastAPI + WebSocket.
  ws://127.0.0.1:8765/ws/voice   — стриминг микрофона (PCM) от клиента игры
  POST /api/world                — Lua-скрипт игры присылает snapshot NPC рядом
  GET  /api/state/<npc_id>       — память/отношение конкретного NPC
  GET  /healthz                  — проверка живости

Цикл: игрок говорит в микрофон -> STT -> кто рядом слышал (радиус) -> LLM отыгрывает
ближайшего NPC -> реплика+действие отправляются обратно в игру (звук/анимация/
отношения фракции). Ответы «подслушавших» союзников генерируются без LLM-вызова.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from config_loader import load_config, ROOT
from llm import make_llm, parse_npc_json, RESPONSE_SCHEMA_HINT  # noqa: F401
from npc_state import MemoryStore
from prompts import build_messages, ally_reaction
from stt import make_stt
from tts import TtsEngine

cfg = load_config()

# ---------------- логирование ----------------
Path(ROOT / "logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=getattr(logging, cfg.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler(ROOT / cfg.log_file, encoding="utf-8"),
              logging.StreamHandler()])
log = logging.getLogger("scnai.server")

app = FastAPI(title="StalkerChN_AI", version="0.1.0")
voice_clients: set[WebSocket] = set()
memory = MemoryStore()
llm = make_llm(cfg)
tts = TtsEngine(cfg)

# STT инициализируем лениво в первом воркере (модель Whisper грузится долго)
_stt_holder: dict = {}


def get_stt():
    if "fn" not in _stt_holder:
        _stt_holder["fn"] = make_stt(cfg)
    return _stt_holder["fn"]


stt_executor = None  # отдельный поток для STT (set в lifespan)

# Мир игры на стороне клиента (последний snapshot)
world: dict = {"player_pos": [0, 0, 0], "npcs": [], "updated": 0.0}
# Очередь событий для Lua-скрипта игры (он забирает их через POST /api/world)
outbox: list[dict] = []
outbox_lock = asyncio.Lock()


async def push_event(ev: dict):
    # параллельно уведомляем микрофон-клиент (браузер/оверлей), если подключён
    for wc in list(voice_clients):
        try:
            await wc.send_json(ev)
        except Exception:  # noqa: BLE001
            voice_clients.discard(wc)
    async with outbox_lock:
        outbox.append(ev)
        # защита от разрастания, если игра не забирает события
        if len(outbox) > 50:
            del outbox[:-50]


# ----------------------------------------------------------------------
# ЯДРО: транскрипт -> слушающие -> LLM -> реакции
# ----------------------------------------------------------------------
async def process_transcript(text: str, heard_radius: float | None = None):
    text = text.strip()
    if not text:
        return
    if cfg.log_transcripts:
        log.info("Игрок сказал: %r", text)

    radius = heard_radius or cfg.reaction.faction_alert_radius
    px, py, pz = world["player_pos"]

    # Кто реально рядом и слышит (по последнему снапшоту мира из Lua)
    listeners = []
    for n in world["npcs"]:
        dx, dy, dz = n["pos"][0] - px, n["pos"][1] - py, n["pos"][2] - pz
        d2 = dx * dx + dy * dy + dz * dz
        if d2 <= radius * radius and not n.get("dead"):
            if cfg.factions_allowed and n["faction"].lower() not in cfg.factions_allowed:
                continue
            listeners.append((d2, n))
    if not listeners:
        await push_event({"type": "notice", "text": "(рядом никого, кто мог бы услышать)"})
        return
    listeners.sort(key=lambda t: t[0])
    speaker = listeners[0][1]          # ближайший — он и отвечает вслух
    others = [n for _, n in listeners[1:]]

    st = memory.get_or_create(
        speaker["id"], name=speaker.get("name", "сталкер"),
        profile=speaker.get("profile", ""), faction=speaker.get("faction", "neutral"),
        position=tuple(speaker["pos"]), health=speaker.get("health", 1.0))
    for n in others:
        memory.get_or_create(n["id"], name=n.get("name", ""),
                             faction=n.get("faction", "neutral"),
                             position=tuple(n["pos"]), health=n.get("health", 1.0))
        memory.touch_heard(n["id"])
    memory.touch_heard(st.npc_id)

    if not memory.can_react(st.npc_id, cfg.reaction.reaction_cooldown):
        return  # анти-спам

    messages = build_messages(st, text,
                              [o.get("name", o["id"]) for o in others],
                              safety=cfg.llm.safety_prompt)
    try:
        raw = await llm.generate(messages, cfg.llm.temperature, cfg.llm.max_tokens)
    except Exception as e:  # noqa: BLE001
        log.error("LLM ошибка: %s", e)
        await push_event({"type": "notice", "text": f"(сервер ИИ недоступен: {e})"})
        return
    resp = parse_npc_json(raw)
    log.info("%s -> %s [%s]", st.name, resp["say"], resp["action"])

    # в память/историю пишем ЧИСТЫЙ транскрипт и чистую реплику (без JSON-мусора)
    rel_new = memory.add_exchange(st.npc_id, text, resp["say"],
                                  resp["mood_delta"], cfg.npc.history_turns)
    if resp["remember"]:
        memory.add_fact(st.npc_id, resp["remember"], cfg.npc.fact_memory_size)
    memory.mark_reacted(st.npc_id)

    audio_path = await tts.synthesize(resp["say"])

    await push_event({
        "type": "reply",
        "npc_id": st.npc_id,
        "say": resp["say"],
        "action": resp["action"],
        "relationship": rel_new,
        "audio": audio_path,
    })

    # --- Эффект для фракции: союзники слышали разговор ---
    alert = resp["action"] in ("offended", "aggressive", "alert_faction") \
        or resp["mood_delta"] <= cfg.reaction.offended_threshold
    friendly_spread = resp["action"] == "friendly" or resp["mood_delta"] >= cfg.reaction.friendly_threshold
    if (alert or friendly_spread) and cfg.reaction.allow_faction_alert:
        delta = (-3 if alert else 2) * max(1, len(others))
        rep = memory.bump_faction_rep(st.faction, delta)
        for o in others:
            ost = memory.npcs[o["id"]]
            ost.relationship += (-4 if alert else 2)
            ost.clamp_rel()
            if alert:
                memory.add_fact(o["id"], f"Слышал, как ты обидел {st.name}",
                                cfg.npc.fact_memory_size)
            ar = ally_reaction("alert_faction" if resp["action"] == "alert_faction"
                               else ("aggressive" if resp["action"] == "aggressive"
                                     else ("offended" if alert else "friendly")),
                               st.name.split()[0] if st.name else "братан")
            if ar:
                await push_event({"type": "ally_reply", "npc_id": o["id"],
                                  "say": ar["say"], "action": ar["action"]})
        await push_event({"type": "faction_rep", "faction": st.faction,
                          "rep": rep, "delta": delta})

    memory.save()


# ----------------------------------------------------------------------
# HTTP API (для Lua-скрипта внутри игры)
# ----------------------------------------------------------------------
@app.post("/api/world")
async def set_world(body: dict):
    """Lua шлёт каждые ~0.5с: позиция игрока + список ближайших NPC."""
    world["player_pos"] = body.get("player_pos", [0, 0, 0])
    world["npcs"] = body.get("npcs", [])
    world["updated"] = time.time()
    events = []
    async with outbox_lock:
        events, outbox[:] = outbox[:], []
    return {"ok": True, "events": events}


@app.get("/api/state/{npc_id}")
async def get_state(npc_id: str):
    st = memory.npcs.get(npc_id)
    if not st:
        return JSONResponse({"error": "unknown npc"}, status_code=404)
    return {
        "npc_id": st.npc_id, "name": st.name, "faction": st.faction,
        "relationship": st.relationship,
        "faction_rep": memory.faction_rep.get(st.faction, 0),
        "facts": st.facts[-10:],
        "history": st.history[-6:],
    }


@app.post("/api/text")
async def post_text(body: dict):
    """Резервный ввод без микрофона: {"text": "фраза игрока"}.
    Полезно, если нет Lua-сетевого хаба или нужно быстро протестировать реакцию NPC."""
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    await process_transcript(text, heard_radius=body.get("radius"))
    return {"ok": True}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "stt": cfg.stt.backend, "llm": cfg.llm.backend,
            "npcs_known": len(memory.npcs)}


# ----------------------------------------------------------------------
# WebSocket: аудио-стрим микрофона
# ----------------------------------------------------------------------
@app.websocket("/ws/voice")
async def ws_voice(ws: WebSocket):
    await ws.accept(subprotocol="scnai-v1")
    voice_clients.add(ws)
    log.info("Клиент игры подключился (голос)")
    buf = bytearray()
    started = 0.0
    heard_state: dict = {}
    try:
        while True:
            msg = await ws.receive()
            if "bytes" in msg and msg["bytes"]:
                chunk = msg["bytes"]
                if chunk[:8] == b"SNAIHDR!":  # служебный заголовок: радиус + позиция игрока
                    try:
                        data = json.loads(chunk[8:].decode())
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    heard_state["radius"] = data.get("radius")
                    if data.get("player_pos"):
                        world["player_pos"] = data["player_pos"]
                    continue
                if not started:
                    started = time.time()
                buf.extend(chunk)
                bytes_per_sec = cfg.audio.sample_rate * (4 if cfg.audio.encoding == "pcm_f32le" else 2)
                dur = len(buf) / bytes_per_sec
                if dur >= cfg.audio.max_utterance_sec:
                    await flush(buf, started, heard_state)
                    buf, started = bytearray(), 0.0
            elif "text" in msg and msg["text"]:
                ctrl = json.loads(msg["text"])
                if ctrl.get("end_of_speech"):
                    await flush(buf, started, heard_state)
                    buf, started = bytearray(), 0.0
                elif ctrl.get("ping"):
                    await ws.send_text(json.dumps({"pong": time.time()}))
    except WebSocketDisconnect:
        voice_clients.discard(ws)
        log.info("Клиент отключился")


async def flush(buf: bytearray, started: float, heard_state: dict | None = None):
    if not buf:
        return
    bytes_per_sec = cfg.audio.sample_rate * (4 if cfg.audio.encoding == "pcm_f32le" else 2)
    dur = len(buf) / bytes_per_sec
    if dur < cfg.audio.min_utterance_sec:
        return
    pcm = bytes(buf)
    loop = asyncio.get_running_loop()
    radius = (heard_state or {}).get("radius")
    try:
        text = await loop.run_in_executor(None, get_stt(), pcm)
    except Exception as e:  # noqa: BLE001
        log.error("STT ошибка: %s", e)
        await push_event({"type": "notice", "text": f"(не расслышал: {e})"})
        return
    if text:
        await process_transcript(text, heard_radius=radius)


if __name__ == "__main__":
    import uvicorn
    log.info("StalkerChN_AI сервер: http://%s:%d (STT=%s, LLM=%s)",
             cfg.server.host, cfg.server.port, cfg.stt.backend, cfg.llm.backend)
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="warning")
