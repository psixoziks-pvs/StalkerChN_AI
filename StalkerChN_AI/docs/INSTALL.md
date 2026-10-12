# Установка StalkerChN_AI

## 0. Что понадобится

* Windows 10/11, Python 3.10+ (сервер ИИ).
* S.T.A.L.K.E.R.: Чистое Небо / Зов Припяти (лучше современные сборки: SRP CoC,
  ZRP — в них уже есть HTTP-хуки для Lua).
* Микрофон. Оффлайн-режим полностью возможен (Vosk + Ollama на CPU).

## 1. Сервер ИИ

### Быстрый путь (Windows)
Дважды щёлкните **`scripts\install.bat`** — он создаст `.venv` и поставит базы.
Запуск сервера — **`scripts\run_server.bat`**.

### Быстрый путь (Linux/macOS)
```bash
./scripts/install.sh && ./scripts/run_server.sh
```

### Ручной путь

```bat
cd StalkerChN_AI
python -m venv .venv
.venv\Scripts\activate
pip install fastapi "uvicorn[standard]" httpx numpy
:: опционально, для реального распознавания речи:
pip install faster-whisper        :: или: pip install vosk
:: опционально, голоса NPC:
pip install edge-tts
```

Запуск: `python scripts\server\main.py` (порт 8765, см. `config/settings.ini`).

Проверка: откройте `http://127.0.0.1:8765/healthz` → `{"ok":true,...}`.

### LLM

Рекомендуется **Ollama** (бесплатно, офлайн):

1. Установите https://ollama.com
2. `ollama pull qwen2.5:7b-instruct`
3. В `[llm] backend = ollama` — готово.

Альтернативы: `openai` (любой OpenAI-совместимый endpoint — LM Studio, vLLM),
`anthropic`, либо `mock` для теста без ИИ-железа.

### STT

* `faster-whisper` — качество выше, модель `small` грузится ~2 ГБ RAM.
* `vosk` — почти ничего не ест; скачайте `vosk-model-small-ru-0.22.zip` с
  alphacephei.com/vosk/models, распакуйте в `models/vosk-ru-0.42` (путь в конфиге).

## 2. Микрофон-мост

Откройте `client/mic_bridge.html` в Chrome/Edge (разрешите микрофон).
Зажатая **V** = передача голоса; после паузы ~0.7 с фраза уходит на сервер.
Мост можно свернуть в отдельный монитор/телефон — ему нужен только доступ к
`ws://127.0.0.1:8765` (для игры на другой машине поправьте `SERVER_WS`).

> Почему браузер? X-Ray не умеет захватывать микрофон из Lua. Если хочется
> «всё в одном окне» — соберуйте любой VOIP-оверлей (VoiceMeeter + Whisper.cpp
> CLI) и подавайте PCM туда же через WebSocket, протокол описан в docs/API.md.

## 3. Клиент внутри игры (X-Ray Lua)

1. Скопируйте папку `game/scripts/AIValley/` в `<игра>/gamedata/scripts/`.
2. Подключите в любом месте, выполняемом на старте уровня, например добавьте
   строку в конец `gamedata/scripts/alife_simulator.lua`:
   ```lua
   require("AIValley/init")
   ```
   (В сборках с xr_effects — аналогично через их менеджер скриптов.)
3. Скопируйте `game/gamedata/texts/st_snai_stable.xml` в
   `<игра>/gamedata/config/texts/` (названия строк HUD).
4. Если включаете TTS: укажите в `settings.ini [tts] cache_dir` путь внутри
   `<игра>/gamedata/sounds/scnai/...` (или сделайте симлинк), чтобы игра видела
   `.ogg` файлы ответов.

### HTTP-шлюз для Lua — важный нюанс

Чистый X-Ray **не делает сетевых запросов из Lua**. `init.lua` автоматически
пробует (в порядке):

| Хук | Где бывает |
|---|---|
| `xr_http.request{...}` | SRP CoC и большинство современных билдов |
| `luaxr.http.post(url, body)` | отдельные сборки Call of Chernobyl |
| `luasocket` (`socket.http`) | если в Lua-машину проброшена luasocket |

Если в вашей сборке нет ни одного — используйте самый простой обходной путь:
оставьте игру без Lua-сетевой части и смотрите ответы в логе сервера +
субтитры моста (браузер получает все события по тому же WS). Полноценный
DLL-мост (tiny http-client, экспортирующий `luaopen_http`) — план следующего
релиза, см. TODO в README.

## 4. Проверка в игре

1. Запустите сервер ИИ, затем игру; подойдите к группе NPC (например бандиты на Кордоне).
2. Откройте мост, зажмите V: «Здорово, сталкеры!»
3. Ожидайте 1–3 с → субтитр от ближайшего NPC (+ его голос, если TTS включён).
4. Оскорбите его при свидетелях → в логе сервера увидите `faction_rep` событие,
   в игре — реплики союзников; перескажите потом `/api/state/<npc_id>` — память живёт.

## 5. Сброс/очистка памяти

Удалите `data/npc_memory.json` при выключенном сервере. Всё хранится по id секций
NPC, так что при переустановке сохранения старые знакомцы сохранятся.
