import logging
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


logger = logging.getLogger("winston.projects")
_PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_MAX_DISCOVERED_PROJECTS = 200


class ProjectConfigurationError(ValueError):
    """Raised when the configured project registry is invalid."""


class ProjectNotFoundError(LookupError):
    """Raised when a configured project cannot be found."""


class _LaunchSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    vscode: bool = False
    terminal: bool = False


class _ProjectSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=2000)
    description: str = Field(default="", max_length=1000)
    launch: _LaunchSettings = Field(default_factory=_LaunchSettings)
    startup_commands: list[str] = Field(default_factory=list, max_length=20)
    metadata: dict[str, Any] = Field(default_factory=dict)


class _ProjectRootSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str = Field(min_length=1, max_length=2000)
    launch: _LaunchSettings = Field(
        default_factory=lambda: _LaunchSettings(vscode=True, terminal=True)
    )

    @model_validator(mode="after")
    def require_launch_action(self) -> "_ProjectRootSettings":
        if not self.launch.vscode and not self.launch.terminal:
            raise ValueError("A project root must enable at least one launch action.")
        return self


class _RegistrySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projects: dict[str, _ProjectSettings] = Field(default_factory=dict, max_length=100)
    project_roots: list[_ProjectRootSettings] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_projects_or_roots(self) -> "_RegistrySettings":
        if not self.projects and not self.project_roots:
            raise ValueError("Configure at least one project or project root.")
        return self


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    path: Path
    description: str
    launch_vscode: bool
    launch_terminal: bool
    startup_commands: tuple[str, ...]
    metadata: dict[str, Any]


class ProjectRegistry:
    def __init__(self, projects: list[Project]) -> None:
        self._projects = {project.id: project for project in projects}
        self._names = {project.name.casefold(): project.id for project in projects}

    @classmethod
    def load(
        cls,
        config_path: Path,
        *,
        additional_roots: list[Path] | None = None,
    ) -> "ProjectRegistry":
        try:
            config_text = config_path.read_text(encoding="utf-8")
            raw_config = yaml.safe_load(config_text)
            settings = _RegistrySettings.model_validate(raw_config)
            projects = [
                cls._build_project(config_path, project_id, project_settings)
                for project_id, project_settings in settings.projects.items()
            ]
            for root_settings in settings.project_roots:
                projects.extend(cls._discover_root(config_path, root_settings))
            explicit_ids = {project.id for project in projects}
            explicit_names = {project.name.casefold() for project in projects}
            for root_path in additional_roots or []:
                root_projects = cls._discover_root(
                    config_path,
                    _ProjectRootSettings(path=str(root_path)),
                )
                projects.extend(
                    project
                    for project in root_projects
                    if project.id not in explicit_ids
                    and project.name.casefold() not in explicit_names
                )
            if not projects:
                raise ProjectConfigurationError(
                    "The project registry contains no available project directories."
                )
            project_ids = [project.id for project in projects]
            if len(set(project_ids)) != len(project_ids):
                raise ProjectConfigurationError(
                    "Project IDs must be unique across configured projects and roots."
                )
            if len({project.name.casefold() for project in projects}) != len(projects):
                raise ProjectConfigurationError(
                    "Project names must be unique, ignoring case."
                )
            if len(projects) > _MAX_DISCOVERED_PROJECTS:
                raise ProjectConfigurationError(
                    f"Project registry may contain at most "
                    f"{_MAX_DISCOVERED_PROJECTS} projects."
                )
            return cls(projects)
        except ProjectConfigurationError as exc:
            logger.error("Project registry configuration is invalid: %s", exc)
            raise
        except (OSError, yaml.YAMLError, ValidationError, TypeError) as exc:
            logger.error(
                "Unable to load project registry at %s: %s",
                config_path,
                exc,
            )
            raise ProjectConfigurationError(
                f"Unable to load project registry at {config_path}."
            ) from exc

    def get_project(self, project_id_or_name: str) -> Project:
        key = project_id_or_name.strip().casefold()
        project_id = key if key in self._projects else self._names.get(key)
        if project_id is None:
            raise ProjectNotFoundError("Configured project was not found.")
        return self._projects[project_id]

    def list_projects(self) -> list[Project]:
        return sorted(self._projects.values(), key=lambda project: project.id)

    @staticmethod
    def _build_project(
        config_path: Path,
        project_id: str,
        settings: _ProjectSettings,
    ) -> Project:
        if not _PROJECT_ID_PATTERN.fullmatch(project_id):
            raise ProjectConfigurationError(
                f"Invalid project identifier: {project_id!r}."
            )
        if not settings.launch.vscode and not settings.launch.terminal:
            raise ProjectConfigurationError(
                f"Project {project_id!r} must enable at least one launch action."
            )

        configured_path = Path(settings.path).expanduser()
        if not configured_path.is_absolute():
            configured_path = config_path.parent / configured_path
        try:
            project_path = configured_path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ProjectConfigurationError(
                f"Project path for {project_id!r} does not exist."
            ) from exc
        if not project_path.is_dir():
            raise ProjectConfigurationError(
                f"Project path for {project_id!r} is not a directory."
            )

        return Project(
            id=project_id,
            name=settings.name.strip(),
            path=project_path,
            description=settings.description,
            launch_vscode=settings.launch.vscode,
            launch_terminal=settings.launch.terminal,
            startup_commands=tuple(settings.startup_commands),
            metadata=settings.metadata,
        )

    @classmethod
    def _discover_root(
        cls,
        config_path: Path,
        settings: _ProjectRootSettings,
    ) -> list[Project]:
        configured_root = Path(settings.path).expanduser()
        if not configured_root.is_absolute():
            configured_root = config_path.parent / configured_root
        try:
            root = configured_root.resolve(strict=True)
            if not root.is_dir():
                raise ProjectConfigurationError(
                    f"Project root {settings.path!r} is not a directory."
                )
            children = []
            for child in root.iterdir():
                if child.name.startswith(".") or child.is_symlink() or not child.is_dir():
                    continue
                if len(children) >= _MAX_DISCOVERED_PROJECTS:
                    raise ProjectConfigurationError(
                        f"Project roots may contain at most "
                        f"{_MAX_DISCOVERED_PROJECTS} directories."
                    )
                children.append(child.resolve(strict=True))
            children.sort(key=lambda child: child.name.casefold())
        except (OSError, RuntimeError) as exc:
            raise ProjectConfigurationError(
                f"Project root {settings.path!r} does not exist or cannot be read."
            ) from exc

        discovered: list[Project] = []
        for path in children:
            project_id = _project_id_from_name(path.name)
            if not _PROJECT_ID_PATTERN.fullmatch(project_id):
                raise ProjectConfigurationError(
                    f"Unable to create a project ID for directory {path.name!r}."
                )
            discovered.append(
                Project(
                    id=project_id,
                    name=path.name,
                    path=path,
                    description=f"Project in {path.parent}",
                    launch_vscode=settings.launch.vscode,
                    launch_terminal=settings.launch.terminal,
                    startup_commands=(),
                    metadata={},
                )
            )
        return discovered


def _project_id_from_name(name: str) -> str:
    project_id = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    if not project_id or not project_id[0].isalpha():
        project_id = f"project-{project_id}"
    return project_id[:64].rstrip("-")
