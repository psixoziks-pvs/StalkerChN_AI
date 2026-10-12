# API StalkerChN_AI

Сервер: `http://127.0.0.1:8765` (настраивается в `config/settings.ini`).

## HTTP

### POST /api/world  — снапшот от Lua-клиента (каждые ~0.5 с)

Запрос:
```json
{
  "player_pos": [x, y, z],
  "npcs": [
    {"id": "stalker_bandit_0001::profile", "name": "Борис", "faction": "bandit",
     "pos": [x, y, z], "health": 0.9, "dead": false, "profile": "(опц. описание)"}
  ]
}
```

Ответ (очередь событий с момента последнего запроса):
```json
{"ok": true, "events": [
  {"type": "reply",        "npc_id": "...", "say": "Текст вслух",
   "action": "friendly|offended|aggressive|alert_faction|neutral|none",
   "relationship": -24, "audio": "C:/.../tts_cache/npc_ab12.ogg"},
  {"type": "ally_reply",   "npc_id": "...", "say": "...", "action": "offended"},
  {"type": "faction_rep",  "faction": "bandit", "rep": -6, "delta": -4},
  {"type": "notice",       "text": "Рядом никого, кто мог бы услышать"}
]}
```

### GET /api/state/{npc_id}
Личное отношение, репутация фракции, последние факты и фрагмент диалога.

### POST /api/text — ввод без микрофона (тест/резервный режим)
```json
{"text": "Здорово, сталкеры!", "radius": 25}
```
Фраза обрабатывается так же, как распознанная речь: ближайший NPC в `radius`
ответит, союзники по фракции отреагируют, отношения обновятся. События придут
в очередь `/api/world` и всем WS-клиентам (микрофон-мост покажет их в логе).
Удобно для быстрой проверки: `curl -X POST http://127.0.0.1:8765/api/text -H "Content-Type: application/json" -d "{\"text\":\"Извини, брат, погорячился\"}"`

### GET /healthz
`{"ok":true,"stt":"faster-whisper","llm":"ollama","npcs_known":37}`

## WebSocket /ws/voice (протокол scnai-v1)

Двунаправленный. Клиент микрофона → сервер:

* **бинарные фреймы** — PCM mono (`encoding` из конфига: `pcm_f32le`/`pcm_s16le`,
  `sample_rate` обычно 16000). Сервер сам сегментирует по `end_of_speech`.
* **текстовый кадр** `{ "end_of_speech": true }` — «отпустил PTT», запускает STT.
* **текстовый кадр** `{ "ping": 1 }` → ответ `{ "pong": <ts> }`.
* **бинарный префикс-заголовок**: 8 байт `SNAIHDR!` + JSON
  `{"radius": 25, "player_pos": [x,y,z]}` — необязательная подсказка радиуса
  слышимости/позиции для текущей фразы (переопределяет снапшот до следующей).

Сервер → клиент микрофона: те же события, что и в `events` выше (удобно для
оверлея субтитров без игры на том же экране).

## Формат ответа LLM (внутренний контракт)

Модель обязана вернуть JSON:
```json
{"say": "строка ≤600 симв.", "mood_delta": -20..20,
 "action": "one of none|neutral|friendly|offended|aggressive|alert_faction|tell_companions",
 "remember": "факт или пусто"}
```
Парсер устойчив к ```json обёрткам и мусору; при провале возвращает сырой текст
как `say` с нейтральным действием.

## Права NPC над игрой (что реально делает Lua-клиент)

| Событие | Действие в игре |
|---|---|
| reply/ally_reply | субтитры; 3D-звук TTS у позиции NPC; `play_action` (greet/anger/alarm) |
| faction_rep | изменение goodwill сообщества к игроку |
| notice | HUD-подсказка |

Автоматического урона/спавна врагов мод **не** наносит — только социальные
последствия (отношения/реплики), чтобы не ломать баланс сборки. При желании
агрессию можно включить: смотрите `allow_faction_alert` и допилите обработчик
`alert_faction` в `init.lua`.
