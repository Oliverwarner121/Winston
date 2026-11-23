from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.tools.base import ToolExecutionError


_SENSITIVE_NAMES = {
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
}
_SENSITIVE_PARTS = {".git", ".ssh", ".aws", ".gnupg", ".config"}
_SENSITIVE_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".secret"}


class WorkspacePathError(ToolExecutionError):
    """The requested path is not a safe path inside a configured workspace."""


@dataclass(frozen=True)
class WorkspacePath:
    root: Path
    path: Path

    @property
    def relative_path(self) -> str:
        return self.path.relative_to(self.root).as_posix()


def resolve_workspace_path(
    requested_path: str,
    roots: Iterable[Path],
    *,
    must_exist: bool = False,
) -> WorkspacePath:
    if not requested_path or "\x00" in requested_path:
        raise WorkspacePathError("A valid workspace path is required.")

    raw_path = Path(requested_path).expanduser()
    for configured_root in roots:
        try:
            root = configured_root.resolve(strict=True)
        except OSError:
            continue
        if not root.is_dir():
            continue

        candidate = raw_path if raw_path.is_absolute() else root / raw_path
        try:
            unresolved_relative_path = candidate.relative_to(root)
        except ValueError:
            continue
        if any(part in {"..", ""} for part in unresolved_relative_path.parts):
            continue

        current = root
        has_symlink = False
        for part in unresolved_relative_path.parts:
            current = current / part
            if current.is_symlink():
                has_symlink = True
                break
        if has_symlink:
            raise WorkspacePathError("Symbolic links are not allowed in tool paths.")

        try:
            resolved_path = candidate.resolve(strict=must_exist)
            resolved_path.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            continue

        return WorkspacePath(root=root, path=resolved_path)

    raise WorkspacePathError("Path is outside the configured workspaces.")


def is_sensitive_path(path: Path) -> bool:
    parts = {part.casefold() for part in path.parts}
    name = path.name.casefold()
    if parts.intersection(_SENSITIVE_PARTS) or name in _SENSITIVE_NAMES:
        return True
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    return path.suffix.casefold() in _SENSITIVE_SUFFIXES
