import asyncio
import json
import os
import stat
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import ToolSettings
from app.tools.base import Tool, ToolContext, ToolExecutionError
from app.tools.implementations.workspace import (
    is_sensitive_path,
    resolve_workspace_path,
)
from app.tools.models import ToolCategory
from app.tools.permissions import PermissionLevel


_EXCLUDED_DIRECTORIES = {
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}


class FileSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200)
    folder: str | None = Field(default=None, max_length=1000)
    extension: str | None = Field(default=None, max_length=20)
    limit: int = Field(default=50, ge=1, le=100)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, query: str) -> str:
        normalized = query.strip()
        if not normalized:
            raise ValueError("Search query must not be empty.")
        return normalized


class FileSearchResult(BaseModel):
    path: str
    size_bytes: int


class FileSearchOutput(BaseModel):
    files: list[FileSearchResult]
    truncated: bool


class FileSearchTool(Tool[FileSearchInput, FileSearchOutput]):
    id = "file_search"
    name = "File search"
    description = "Find files by name within configured workspace roots."
    category = ToolCategory.FILES
    permission_level = PermissionLevel.READ
    input_model = FileSearchInput
    output_model = FileSearchOutput

    def __init__(self, settings: ToolSettings) -> None:
        self._settings = settings

    async def execute(
        self,
        inputs: FileSearchInput,
        context: ToolContext,
    ) -> FileSearchOutput:
        return await asyncio.to_thread(self._search, inputs)

    def _search(self, inputs: FileSearchInput) -> FileSearchOutput:
        roots: list[Path]
        if inputs.folder is not None:
            location = resolve_workspace_path(inputs.folder, self._settings.workspace_roots)
            if is_sensitive_path(location.path):
                raise ToolExecutionError("Searching sensitive folders is not allowed.")
            if not location.path.is_dir():
                raise ToolExecutionError("Search folder was not found.")
            roots = [location.path]
        else:
            roots = []
            for root in self._settings.workspace_roots:
                try:
                    resolved_root = root.resolve(strict=True)
                except OSError as exc:
                    raise ToolExecutionError(
                        "A configured workspace is unavailable."
                    ) from exc
                if not resolved_root.is_dir():
                    raise ToolExecutionError(
                        "A configured workspace is not a directory."
                    )
                roots.append(resolved_root)

        if not roots:
            raise ToolExecutionError("No configured workspace is available.")

        query = inputs.query.casefold()
        extension = inputs.extension
        if extension:
            extension = extension.removeprefix(".").casefold()
        limit = min(inputs.limit, self._settings.max_search_results)
        matches: list[FileSearchResult] = []
        has_more = False
        scanned = 0

        for root in roots:
            for current, directories, filenames in os.walk(
                root,
                followlinks=False,
                onerror=_raise_walk_error,
            ):
                current_path = Path(current)
                directories[:] = [
                    name
                    for name in directories
                    if not name.startswith(".")
                    and name.casefold() not in _EXCLUDED_DIRECTORIES
                    and not (current_path / name).is_symlink()
                ]
                for filename in filenames:
                    scanned += 1
                    if scanned > self._settings.max_search_files_scanned:
                        has_more = True
                        break
                    if filename.startswith(".") or query not in filename.casefold():
                        continue
                    path = current_path / filename
                    if path.is_symlink() or is_sensitive_path(path):
                        continue
                    if extension and path.suffix.casefold().removeprefix(".") != extension:
                        continue
                    if len(matches) >= limit:
                        has_more = True
                        break
                    try:
                        size_bytes = path.stat(follow_symlinks=False).st_size
                    except (OSError, ValueError):
                        continue
                    matches.append(
                        FileSearchResult(path=str(path), size_bytes=size_bytes)
                    )
                if has_more:
                    break
            if has_more:
                break

        return FileSearchOutput(files=matches, truncated=has_more)


class FileReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1000)


class FileReadOutput(BaseModel):
    path: str
    content: str


class FileReadTool(Tool[FileReadInput, FileReadOutput]):
    id = "file_read"
    name = "File read"
    description = "Read a bounded UTF-8 text file inside a workspace."
    category = ToolCategory.FILES
    permission_level = PermissionLevel.READ
    input_model = FileReadInput
    output_model = FileReadOutput
    audit_safe_input_fields: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, settings: ToolSettings) -> None:
        self._settings = settings

    async def execute(
        self,
        inputs: FileReadInput,
        context: ToolContext,
    ) -> FileReadOutput:
        location = resolve_workspace_path(
            inputs.path,
            self._settings.workspace_roots,
            must_exist=True,
        )
        if is_sensitive_path(location.path):
            raise ToolExecutionError("Reading sensitive files is not allowed.")
        if not location.path.is_file():
            raise ToolExecutionError("Requested path is not a file.")

        try:
            content = await asyncio.to_thread(
                _read_bounded_text,
                location.path,
                self._settings.max_file_size_bytes,
            )
        except UnicodeDecodeError as exc:
            raise ToolExecutionError("Only UTF-8 text files can be read.") from exc
        except ToolExecutionError:
            raise
        except OSError as exc:
            raise ToolExecutionError("Unable to read the requested file.") from exc

        return FileReadOutput(path=location.relative_path, content=content)


class FileWriteMode(StrEnum):
    CREATE = "create"
    OVERWRITE = "overwrite"
    APPEND = "append"


class FileWriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1000)
    content: str = Field(max_length=4000)
    mode: FileWriteMode = FileWriteMode.CREATE


class FileWriteOutput(BaseModel):
    path: str
    bytes_written: int


class FileWriteTool(Tool[FileWriteInput, FileWriteOutput]):
    id = "file_write"
    name = "File write"
    description = "Create, overwrite, or append to a text file in a workspace."
    category = ToolCategory.FILES
    permission_level = PermissionLevel.WRITE
    requires_confirmation = True
    input_model = FileWriteInput
    output_model = FileWriteOutput
    audit_safe_input_fields: ClassVar[frozenset[str]] = frozenset({"path", "mode"})

    def __init__(self, settings: ToolSettings) -> None:
        self._settings = settings

    def required_permission_level(self, inputs: FileWriteInput) -> PermissionLevel:
        if inputs.mode == FileWriteMode.OVERWRITE:
            return PermissionLevel.ADMIN
        return PermissionLevel.WRITE

    def confirmation_message(self, inputs: FileWriteInput) -> str:
        return (
            f"{inputs.mode.value.capitalize()} {json.dumps(inputs.path)} "
            f"with content {json.dumps(inputs.content, ensure_ascii=False)}."
        )

    async def execute(
        self,
        inputs: FileWriteInput,
        context: ToolContext,
    ) -> FileWriteOutput:
        location = resolve_workspace_path(inputs.path, self._settings.workspace_roots)
        if is_sensitive_path(location.path):
            raise ToolExecutionError("Writing sensitive files is not allowed.")
        if not location.path.parent.is_dir():
            raise ToolExecutionError("Parent directory does not exist.")
        if location.path.exists() and not location.path.is_file():
            raise ToolExecutionError("Requested path is not a file.")
        if inputs.mode == FileWriteMode.CREATE and location.path.exists():
            raise ToolExecutionError("File already exists; choose overwrite explicitly.")
        if (
            inputs.mode == FileWriteMode.OVERWRITE
            and not location.path.exists()
        ):
            raise ToolExecutionError("File to overwrite does not exist.")

        content_bytes = inputs.content.encode("utf-8")
        if len(content_bytes) > self._settings.max_file_size_bytes:
            raise ToolExecutionError("Write would exceed the configured size limit.")

        try:
            await asyncio.to_thread(
                _write_bounded_file,
                location.path,
                content_bytes,
                inputs.mode,
                self._settings.max_file_size_bytes,
            )
        except FileExistsError as exc:
            raise ToolExecutionError("File already exists.") from exc
        except OSError as exc:
            raise ToolExecutionError("Unable to write the requested file.") from exc

        return FileWriteOutput(
            path=location.relative_path,
            bytes_written=len(content_bytes),
        )


class FileMoveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=1000)
    destination: str = Field(min_length=1, max_length=1000)


class FileMoveOutput(BaseModel):
    source: str
    destination: str


class FileMoveTool(Tool[FileMoveInput, FileMoveOutput]):
    id = "file_move"
    name = "File move"
    description = "Move or rename a file inside configured workspaces."
    category = ToolCategory.FILES
    permission_level = PermissionLevel.ADMIN
    requires_confirmation = True
    input_model = FileMoveInput
    output_model = FileMoveOutput
    audit_safe_input_fields: ClassVar[frozenset[str]] = frozenset(
        {"source", "destination"}
    )

    def __init__(self, settings: ToolSettings) -> None:
        self._settings = settings

    def confirmation_message(self, inputs: FileMoveInput) -> str:
        return (
            f"Move {json.dumps(inputs.source)} to "
            f"{json.dumps(inputs.destination)}."
        )

    async def execute(
        self,
        inputs: FileMoveInput,
        context: ToolContext,
    ) -> FileMoveOutput:
        source = resolve_workspace_path(
            inputs.source,
            self._settings.workspace_roots,
            must_exist=True,
        )
        destination = resolve_workspace_path(
            inputs.destination,
            self._settings.workspace_roots,
        )
        if is_sensitive_path(source.path) or is_sensitive_path(destination.path):
            raise ToolExecutionError("Moving sensitive files is not allowed.")
        if not source.path.is_file():
            raise ToolExecutionError("Source path is not a file.")
        if not destination.path.parent.is_dir():
            raise ToolExecutionError("Destination parent directory does not exist.")
        if destination.path.exists():
            raise ToolExecutionError("Destination already exists.")

        try:
            await asyncio.to_thread(
                _move_without_replacing,
                source.path,
                destination.path,
            )
        except OSError as exc:
            raise ToolExecutionError("Unable to move the requested file.") from exc
        return FileMoveOutput(
            source=source.relative_path,
            destination=destination.relative_path,
        )


def _move_without_replacing(source: Path, destination: Path) -> None:
    os.link(source, destination, follow_symlinks=False)
    try:
        os.unlink(source)
    except OSError:
        os.unlink(destination)
        raise


def _raise_walk_error(error: OSError) -> None:
    raise ToolExecutionError("Unable to search part of the configured workspace.") from error


def _read_bounded_text(path: Path, size_limit: int) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ToolExecutionError("Only regular text files can be read.")
        with os.fdopen(descriptor, "rb") as file:
            descriptor = -1
            data = file.read(size_limit + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(data) > size_limit:
        raise ToolExecutionError("File exceeds the configured size limit.")
    return data.decode("utf-8")


def _write_bounded_file(
    path: Path,
    content: bytes,
    mode: FileWriteMode,
    size_limit: int,
) -> None:
    flags = os.O_WRONLY | getattr(os, "O_NONBLOCK", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if mode == FileWriteMode.CREATE:
        flags |= os.O_CREAT | os.O_EXCL
    elif mode == FileWriteMode.APPEND:
        flags |= os.O_CREAT | os.O_APPEND

    descriptor = os.open(path, flags, 0o600)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ToolExecutionError("Only regular files can be written.")
        if (
            mode == FileWriteMode.APPEND
            and file_stat.st_size + len(content) > size_limit
        ):
            raise ToolExecutionError("Write would exceed the configured size limit.")
        if mode == FileWriteMode.OVERWRITE:
            os.ftruncate(descriptor, 0)
        with os.fdopen(
            descriptor,
            "ab" if mode == FileWriteMode.APPEND else "wb",
        ) as file:
            descriptor = -1
            file.write(content)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
