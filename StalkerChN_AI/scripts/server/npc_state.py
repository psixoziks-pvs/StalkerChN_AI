"""Модель NPC и память мода: кто слышал, что помнит, как относится.

Хранение: JSON-файл data/npc_memory.json (переживает перезапуск игры/сервера).
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
MEMORY_FILE = DATA_DIR / "npc_memory.json"


@dataclass
class NpcState:
    """Состояние одного NPC в мире игры."""
    npc_id: str = ""                # уникальный id из игры (section name)
    name: str = "сталкер"           # отображаемое имя
    profile: str = ""               # описание персонажа для LLM (роль, характер)
    faction: str = "neutral"        # dolg | bandit | freedom | army | duty ...
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    health: float = 1.0             # 0..1 — раненый NPC говорит иначе
    is_dead: bool = False
    relationship: int = 0           # -100..+100 личное отношение к игроку
    last_heard_ts: float = 0.0      # когда в последний раз слышал игрока
    last_reaction_ts: float = 0.0   # анти-спам кулдаун
    history: list[dict] = field(default_factory=list)   # [{role, content, ts}]
    facts: list[str] = field(default_factory=list)      # долговременная память
    tags: list[str] = field(default_factory=list)       # "ranque", "trader"...

    def clamp_rel(self):
        self.relationship = max(-100, min(100, self.relationship))


class MemoryStore:
    """Потокобезопасное хранилище состояний NPC + отношений фракций."""

    def __init__(self, path: Path = MEMORY_FILE):
        self._path = path
        self._lock = threading.RLock()
        self.npcs: dict[str, NpcState] = {}
        # отношение фракции к игроку: faction -> int (-100..100)
        self.faction_rep: dict[str, int] = {}
        self.load()

    # ---------- persistence ----------
    def load(self):
        with self._lock:
            if self._path.exists():
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                for k, v in raw.get("npcs", {}).items():
                    st = NpcState(**v)
                    st.position = tuple(v.get("position", (0, 0, 0)))
                    self.npcs[k] = st
                self.faction_rep = raw.get("faction_rep", {})

    def save(self):
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            out = {
                "npcs": {k: asdict(v) for k, v in self.npcs.items()},
                "faction_rep": self.faction_rep,
            }
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self._path)

    # ---------- api ----------
    def get_or_create(self, npc_id: str, **defaults) -> NpcState:
        with self._lock:
            st = self.npcs.get(npc_id)
            if st is None:
                st = NpcState(npc_id=npc_id, **defaults)
                self.npcs[npc_id] = st
            else:
                # обновляем живые данные мира
                for key in ("name", "profile", "faction", "position", "health"):
                    if key in defaults and defaults[key]:
                        setattr(st, key, defaults[key])
            return st

    def touch_heard(self, npc_id: str):
        with self._lock:
            self.npcs[npc_id].last_heard_ts = time.time()

    def can_react(self, npc_id: str, cooldown: float) -> bool:
        with self._lock:
            st = self.npcs.get(npc_id)
            if not st:
                return False
            return (time.time() - st.last_reaction_ts) >= cooldown

    def mark_reacted(self, npc_id: str):
        with self._lock:
            if npc_id in self.npcs:
                self.npcs[npc_id].last_reaction_ts = time.time()

    def add_exchange(self, npc_id: str, player_text: str, npc_reply: str,
                     mood_delta: int, limit: int = 12):
        """Записать диалог и сдвинуть личное отношение; вернуть новое значение."""
        with self._lock:
            st = self.npcs[npc_id]
            now = time.time()
            st.history.append({"role": "player", "content": player_text, "ts": now})
            st.history.append({"role": "npc", "content": npc_reply, "ts": now})
            st.history = st.history[-limit * 2:]
            st.relationship += mood_delta
            st.clamp_rel()
            return st.relationship

    def add_fact(self, npc_id: str, fact: str, limit: int = 30):
        with self._lock:
            st = self.npcs[npc_id]
            if fact and fact not in st.facts:
                st.facts.append(fact)
                st.facts = st.facts[-limit:]

    def bump_faction_rep(self, faction: str, delta: int) -> int:
        """Изменить репутацию игрока во всей фракции (эффект «услышанный разговор»)."""
        with self._lock:
            cur = self.faction_rep.get(faction, 0) + delta
            cur = max(-100, min(100, cur))
            self.faction_rep[faction] = cur
            return cur

    def nearby_listeners(self, pos: tuple[float, float, float], radius: float,
                         exclude: str | None = None) -> list[NpcState]:
        """Все живые NPC в радиусе слышимости от точки говорящего."""
        with self._lock:
            out = []
            r2 = radius * radius
            for st in self.npcs.values():
                if st.is_dead or st.npc_id == exclude:
                    continue
                dx = st.position[0] - pos[0]
                dy = st.position[1] - pos[1]
                dz = st.position[2] - pos[2]
                if dx * dx + dy * dy + dz * dz <= r2:
                    out.append(st)
            return out
