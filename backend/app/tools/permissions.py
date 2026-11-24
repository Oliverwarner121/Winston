from collections.abc import Iterable, Mapping
from enum import StrEnum
from threading import RLock


class PermissionLevel(StrEnum):
    READ = "READ"
    WRITE = "WRITE"
    ADMIN = "ADMIN"
    SYSTEM = "SYSTEM"


_CAPABILITIES: dict[PermissionLevel, frozenset[PermissionLevel]] = {
    PermissionLevel.READ: frozenset({PermissionLevel.READ}),
    PermissionLevel.WRITE: frozenset({PermissionLevel.READ, PermissionLevel.WRITE}),
    PermissionLevel.ADMIN: frozenset(
        {PermissionLevel.READ, PermissionLevel.WRITE, PermissionLevel.ADMIN}
    ),
    PermissionLevel.SYSTEM: frozenset(PermissionLevel),
}


class PermissionManager:
    """Stores explicit actor grants; an actor has no access until granted."""

    def __init__(
        self,
        grants: Mapping[str, Iterable[PermissionLevel]] | None = None,
    ) -> None:
        self._grants: dict[str, set[PermissionLevel]] = {}
        self._lock = RLock()
        for actor_id, levels in (grants or {}).items():
            for level in levels:
                self.grant(actor_id, level)

    def grant(self, actor_id: str, level: PermissionLevel) -> None:
        normalized_actor_id = actor_id.strip()
        if not normalized_actor_id or len(normalized_actor_id) > 200:
            raise ValueError("Actor ID must contain 1 to 200 characters.")

        with self._lock:
            self._grants.setdefault(normalized_actor_id, set()).add(level)

    def revoke(self, actor_id: str, level: PermissionLevel) -> None:
        normalized_actor_id = actor_id.strip()
        with self._lock:
            grants = self._grants.get(normalized_actor_id)
            if grants is None:
                return

            grants.discard(level)
            if not grants:
                del self._grants[normalized_actor_id]

    def is_allowed(self, actor_id: str, required: PermissionLevel) -> bool:
        normalized_actor_id = actor_id.strip()
        if not normalized_actor_id:
            return False

        with self._lock:
            grants = self._grants.get(normalized_actor_id, set()).copy()

        return any(required in _CAPABILITIES[level] for level in grants)
