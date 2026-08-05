"""Small, closed TOML configuration with explicit precedence."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .domain import CAPABILITY_ID_RE, DenimError, bounded_string_list, bounded_token

ORIGINS = ("custom", "builtin", "plugin", "fallback")
MAX_CONFIG_BYTES = 256 * 1024


@dataclass(frozen=True)
class Route:
    use: str
    allow_fallback: bool


@dataclass(frozen=True)
class Annotation:
    provides: tuple[str, ...] | None = None
    priority: int | None = None


@dataclass
class ResolutionConfig:
    order: tuple[str, ...] = ORIGINS
    order_explicit: bool = False
    routes: dict[str, Route] = field(default_factory=dict)
    capabilities: dict[str, Annotation] = field(default_factory=dict)

    def overlay(self, other: ResolutionConfig) -> None:
        if other.order_explicit:
            self.order = other.order
        self.routes.update(other.routes)
        self.capabilities.update(other.capabilities)


def load_config(workspace: Path, *, home: Path | None = None) -> ResolutionConfig:
    """Load built-in, user, then workspace configuration."""

    result = ResolutionConfig()
    user_home = home if home is not None else Path.home()
    for path in (user_home / ".config/denim/config.toml", workspace / ".denim.toml"):
        if path.exists() or path.is_symlink():
            result.overlay(_read(path))
    return result


def _read(path: Path) -> ResolutionConfig:
    if path.is_symlink() or not path.is_file():
        raise DenimError(
            "DENIM_CONFIG_INVALID", "Denim configuration must be a regular file"
        )
    try:
        content = path.read_bytes()
    except OSError as error:
        raise DenimError(
            "DENIM_CONFIG_INVALID", "Denim configuration cannot be read"
        ) from error
    if len(content) > MAX_CONFIG_BYTES:
        raise DenimError(
            "DENIM_CONFIG_INVALID", "Denim configuration exceeds the size limit"
        )
    try:
        value = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise DenimError(
            "DENIM_CONFIG_INVALID", "Denim configuration is not valid TOML"
        ) from error
    if (
        set(value) - {"version", "resolution", "routes", "capabilities"}
        or type(value.get("version")) is not int
        or value["version"] != 1
    ):
        raise DenimError(
            "DENIM_CONFIG_INVALID", "Denim configuration fields are invalid"
        )

    resolution = value.get("resolution", {})
    if not isinstance(resolution, dict) or set(resolution) - {"order"}:
        raise DenimError("DENIM_CONFIG_INVALID", "Resolution configuration is invalid")
    order_value = resolution.get("order", list(ORIGINS))
    if (
        not isinstance(order_value, list)
        or tuple(order_value) != tuple(dict.fromkeys(order_value))
        or set(order_value) != set(ORIGINS)
        or not all(isinstance(item, str) for item in order_value)
    ):
        raise DenimError(
            "DENIM_CONFIG_INVALID", "Resolution order must contain each origin once"
        )

    routes_value = value.get("routes", {})
    if not isinstance(routes_value, dict):
        raise DenimError("DENIM_CONFIG_INVALID", "Routes configuration is invalid")
    routes: dict[str, Route] = {}
    for name, route in routes_value.items():
        bounded_token(name, "Route name")
        if not isinstance(route, dict) or set(route) != {"use", "allow_fallback"}:
            raise DenimError("DENIM_CONFIG_INVALID", "Route fields are invalid")
        use = route.get("use")
        allow_fallback = route.get("allow_fallback")
        if (
            not isinstance(use, str)
            or CAPABILITY_ID_RE.fullmatch(use) is None
            or type(allow_fallback) is not bool
        ):
            raise DenimError("DENIM_CONFIG_INVALID", "Route values are invalid")
        routes[name] = Route(use, allow_fallback)

    annotations_value = value.get("capabilities", [])
    if not isinstance(annotations_value, list) or len(annotations_value) > 256:
        raise DenimError("DENIM_CONFIG_INVALID", "Capability annotations are invalid")
    annotations: dict[str, Annotation] = {}
    for item in annotations_value:
        if (
            not isinstance(item, dict)
            or set(item) - {"id", "provides", "priority"}
            or "id" not in item
        ):
            raise DenimError(
                "DENIM_CONFIG_INVALID", "Capability annotation fields are invalid"
            )
        identifier = item["id"]
        if (
            not isinstance(identifier, str)
            or CAPABILITY_ID_RE.fullmatch(identifier) is None
            or identifier in annotations
        ):
            raise DenimError(
                "DENIM_CONFIG_INVALID", "Capability annotation identity is invalid"
            )
        provides = item.get("provides")
        normalized_provides = (
            tuple(sorted(bounded_string_list(provides, "Capability provides")))
            if provides is not None
            else None
        )
        priority = item.get("priority")
        if priority is not None and (
            type(priority) is not int or not -10_000 <= priority <= 10_000
        ):
            raise DenimError("DENIM_CONFIG_INVALID", "Capability priority is invalid")
        annotations[identifier] = Annotation(normalized_provides, priority)
    return ResolutionConfig(
        order=tuple(order_value),
        order_explicit="order" in resolution,
        routes=routes,
        capabilities=annotations,
    )
