# Голоса NPC (TTS) в StalkerChN_AI

По умолчанию выключено (`[tts] enabled = false`) — ответы идут субтитрами.
Включается для полного «AIValley-ощущения».

| Бэкенд | Установка | Качество ru | Ресурсы |
|---|---|---|---|
| `edge` | `pip install edge-tts` | отличное (Dmitry/Serafima) | нужен интернет |
| `kokoro` | `pip install kokoro soundfile` + модели HF | хорошо | ~2 ГБ RAM/CPU норм |
| `piper` | бинарник piper + onnx-модель `ru_RU` | приемлемо | очень лёгкий |
| `espeak` | `apt/choco espeak-ng` | механический | почти ничего |

Как это попадает в игру: сервер кладёт `.ogg` в `tts_cache/`, Lua играет его через
`CreateSoundEx("scnai:<файл>")` с позицией у NPC — то есть звук пространственный.
Чтобы игра видела файлы, направьте `[tts] cache_dir` в
`<игра>/gamedata/sounds/scnai/` или сделайте симлинк/копирование (см. INSTALL.md).

Совет по стилю: разные голоса на фракции легко делаются словарём voice-id по
npc_id в `tts.py` (пока используется один общий `voice`).
