-- =====================================================================
-- StalkerChN_AI :: init (X-Ray engine / Чистое Небо, Зов Припяти)
-- Подключение: в alife_simulator.lua (или xr_effects) добавить:
--   require("AIValley/init")
-- Либо через universal_spawners/xml_manager по вкусу сборки.
--
-- Что делает этот файл:
--   1. Регистрирует update-цикл алфавита, который каждые 0.5с собирает
--      snapshot ближайших NPC и отправляет на локальный сервер мода
--      (http://127.0.0.1:8765/api/world).
--   2. Забирает события из ответа сервера: реплики NPC, реакции фракций.
--   3. Показывает субтитры (CTA_HUD), играет TTS-звук рядом с NPC
--      (alife_play_sound с привязкой к позиции), меняет отношение через
--      alife_change_goodwill / reputation.
--
-- ВАЖНО про HTTP: чистый X-Ray не умеет сетевые запросы из Lua. Модуль
-- требует любой доступный http-шлюз:
--   * xr_http.request(url, method, body, headers) — есть в сборках типа
--     STALKER Online / SRP Call of Chernobyl / ZRP (современные билды);
--   * либо встроенный lworld/luasocket (реже).
-- Функция scnai_http ниже автоматически выбирает то, что есть. Если ничего
-- нет — см. docs/INSTALL.md, раздел «HTTP-шлюз» (крохотный DLL/EXE-мост).
-- =====================================================================

local scnai = {
    server = "http://127.0.0.1:8765",
    tick = 0.5,                 -- период отправки снапшота, сек
    hear_radius = 25.0,         -- кто слышит голос игрока, м
    acc = 0,
    last_events = {},
}

-- ------------------------- HTTP-обёртка ------------------------------
-- Возвращает тело ответа (string) или nil.
local function scnai_http_post(url, body)
    -- Вариант 1: xr_http — есть в современных сборках (SRP CoC, ZRP-подобные билды)
    if xr_http and xr_http.request then
        local ok, res = pcall(xr_http.request, {
            url = url, method = "POST", body = body or "",
            headers = { ["Content-Type"] = "application/json" },
        })
        if ok then return res end
    end
    -- Вариант 2: luaxr.http (некоторые билды Call of Chernobyl)
    if luaxr and luaxr.http and luaxr.http.post then
        local ok, res = pcall(luaxr.http.post, url, body or "",
                              { ["Content-Type"] = "application/json" })
        if ok then return res end
    end
    -- Вариант 3: luasocket, если проброшена в Lua-машину мода
    local sok_ok, http = pcall(require, "socket.http")
    if sok_ok and http then
        local ltn_ok, ltn12 = pcall(require, "ltn12")
        if ltn_ok then
            local resp, code = {}, http.request{
                url = url, method = "POST",
                source = ltn12.source.string(body or ""),
                headers = { ["content-type"] = "application/json",
                            ["content-length"] = tostring(#(body or "")) },
                sink = ltn12.sink.table(resp),
            }
            if code == 200 then return table.concat(resp) end
        end
    end
    return nil -- сервер недоступен
end

-- JSON: используем встроенный json у движка (есть почти везде), иначе мини-парсер
local json_encode, json_decode
if type(json) == "table" and json.encode then
    json_encode, json_decode = json.encode, json.decode
elseif xr_json and xr_json.encode then
    json_encode, json_decode = xr_json.encode, xr_json.decode
else
    -- Fallback: очень простой сериализатор для нашего формата + ошибка на decode
    json_encode = function(t) error("scnai: нужен JSON-модуль") end
    json_decode = function(s) error("scnai: нужен JSON-модуль") end
end

-- ------------------------- Снапшот мира ------------------------------
local function collect_nearby_npcs()
    -- Обход alife-Lua-представлений NPC: берём актуальные секции из save-файла
    local npcs = {}
    local px, py, pz = 0, 0, 0
    local po = get_player_obj and get_player_obj() or nil
    if po then
        local ppos = po:position()
        px, py, pz = ppos.x, ppos.y, ppos.z
    end
    -- alife_simulator хранит таблицу всех живых объектов мира (obj_table).
    -- В разных сборках имя может отличаться: obj_table / all_objects / alife_table.
    local objects = rawget(_G, "obj_table")
                 or (alife_simulator and alife_simulator.obj_table)
                 or rawget(_G, "all_objects") or {}
    for _, obj in pairs(objects) do
        local ok, section = pcall(function() return obj:section() end)
        if ok and section and string.find(section, "^stalker") then
            local alive = false
            pcall(function() alive = obj:alive() end)
            local pos = { x = px, y = py, z = pz }
            pcall(function() local q = obj:position(); pos = { x = q.x, y = q.y, z = q.z } end)
            local dist = math.sqrt((pos.x - px)^2 + (pos.y - py)^2 + (pos.z - pz)^2)
            if dist <= scnai.hear_radius * 1.5 then
                local fac = "neutral"
                pcall(function() fac = obj:faction() or fac end)
                local nm = section
                pcall(function() nm = obj:name() or nm end)
                local hp = 100.0
                pcall(function() hp = obj:health() or 100.0 end)
                npcs[#npcs + 1] = {
                    id = section, name = tostring(nm), faction = tostring(fac),
                    pos = { pos.x, pos.y, pos.z },
                    health = hp / 100.0, dead = not alive,
                }
            end
        end
    end
    return { player_pos = { px, py, pz }, npcs = npcs }
end

-- ------------------------- Применение событий ------------------------
local function show_subtitle(text, speaker)
    -- CTA_HUD / стандартный детектор диалогов: разные сборки по-разному.
    -- Пробуем несколько распространённых API.
    local line = string.format("[%s]: %s", speaker or "Зона", text)
    -- 1) стандартный HUD-текст X-Ray
    if ui_hud_message then
        pcall(ui_hud_message, line)
    end
    -- 2) кросс-модный хук (детекторы диалогов/CTA-сборки)
    if scnai_hud_hook then pcall(scnai_hud_hook, line) end
    -- 3) лог всегда
    if log then pcall(log, "[StalkerChN_AI] " .. line) end
end

local function play_npc_voice(npc_section, audio_path)
    -- TTS-файл кладется сервером в gamedata/sounds/scnai/ (см. settings.ini cache_dir
    -- + симлинк). Проигрываем как 3D-звук у позиции NPC.
    if not audio_path then return end
    local rel = audio_path:match("scnai[/\\](.+)$")
    if not rel then return end
    local obj = get_object_by_section_name and get_object_by_section_name(npc_section)
    local pos = obj and obj:position() or vector3d.new(0, 0, 0)
    local snd = CreateSoundEx("scnai:" .. rel, false, true, false, 2, 15)
    if snd then
        snd:set_position(pos)
        snd:play(true)
    end
end

local function apply_event(ev, npc_pos_cache)
    if ev.type == "reply" then
        local st = npc_pos_cache[ev.npc_id]
        show_subtitle(ev.say, st and st.name or ev.npc_id)
        play_npc_voice(ev.npc_id, ev.audio)
        -- Анимации: удивление/гнев/дружелюбие (стандартные экшены X-Ray)
        local obj = get_object_by_section_name and get_object_by_section_name(ev.npc_id)
        if obj and obj.play_action then
            local act = ({ friendly = "greet", offended = "anger_aggressive",
                            aggressive = "weapon_draw", alert_faction = "alarm" })[ev.action]
            if act then pcall(function() obj:play_action("general/" .. act) end) end
        end
    elseif ev.type == "ally_reply" then
        local st = npc_pos_cache[ev.npc_id]
        show_subtitle(ev.say, st and st.name or ev.npc_id)
        play_npc_voice(ev.npc_id, ev.audio)
    elseif ev.type == "faction_rep" then
        -- Меняем goodwill всей фракции к игроку (X-Ray: alife_change_goodwill или
        -- community goodwills в зависимости от сборки)
        local delta = ev.delta or 0
        if alife_change_goodwill then
            pcall(alife_change_goodwill, ev.faction, delta)
        elseif change_relation_to_community then
            pcall(change_relation_to_community, ev.faction,
                  (delta < 0) and -1 or 1, math.abs(delta))
        end
        show_subtitle(string.format("Репутация (%s): %+d -> %+d",
                                    ev.faction, delta, ev.rep), "Зона")
    elseif ev.type == "notice" then
        show_subtitle(ev.text, "")
    end
end

-- ------------------------- Главный цикл ------------------------------
local npc_pos_cache = {}

function scnai_update(dt)
    scnai.acc = scnai.acc + dt
    if scnai.acc < scnai.tick then return end
    scnai.acc = 0

    local snap = collect_nearby_npcs()
    for _, n in ipairs(snap.npcs) do npc_pos_cache[n.id] = n end

    local ok, resp = pcall(function()
        return scnai_http_post(scnai.server .. "/api/world", json_encode(snap))
    end)
    if not ok or not resp then return end
    local data = nil
    pcall(function() data = json_decode(resp) end)
    if data and data.events then
        for _, ev in ipairs(data.events) do
            apply_event(ev, npc_pos_cache)
        end
    end
end

-- Хук обновления: предпочтительно встраиваемся в alife_simulator.update,
-- если его нет (например, скрипт подключён из level_update) — используем
-- глобальную функцию update_level, оборачивая существующую.
local function hook_into(target_table, key)
    if type(target_table) ~= "table" then return false end
    local base = target_table[key]
    target_table[key] = function(...)
        if base then base(...) end
        local ok, err = pcall(scnai_update, (select(1, ...) or 0))
        if not ok and log then pcall(log, "[StalkerChN_AI] update err: " .. tostring(err)) end
    end
    return true
end

local hooked = hook_into(rawget(_G, "alife_simulator"), "update")
if not hooked then
    hooked = hook_into(rawget(_G, "level_action"), "update") -- некоторые сборки
end
if not hooked then
    -- последний вариант: объявляем свою функцию и просим вызвать её из level_update.lua
    _G.scnai_update_hook = scnai_update
end

if log then pcall(log, "[StalkerChN_AI] Lua-клиент загружен. Сервер: " .. scnai.server) end

return scnai
