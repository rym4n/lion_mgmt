"""Fail-closed deterministic parser selection."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from services.network_topology.contracts import (
    ContractError,
    load_registry,
    resolve_capability,
)

from .base import DeterministicParser, ParserError, ParserInput
from .structured import StructuredParser


@dataclass(frozen=True)
class _Selection:
    kind: str
    data_schema: str
    vendor: str
    platform: str
    os_family: str
    parser_profile: str | None


class ParserRegistry:
    """Resolve exactly one parser from all required selection dimensions."""

    def __init__(self, parsers: dict[str, DeterministicParser]) -> None:
        self._parsers = dict(parsers)
        bundle = load_registry("aioe.network-topology.registry/parser-bundle/v1")
        self._entries = tuple(bundle["deterministic_parsers"])
        kinds = load_registry("aioe.network-topology.registry/observation-kinds/v1")[
            "kinds"
        ]
        self._schemas = {item["kind"]: item["schema_uri"] for item in kinds}

    @classmethod
    @lru_cache(maxsize=1)
    def default(cls) -> "ParserRegistry":
        parser = StructuredParser()
        return cls({parser.parser_id: parser})

    def resolve(
        self,
        *,
        kind: str,
        data_schema: str,
        vendor: str,
        platform: str,
        os_family: str,
        parser_profile: str | None,
        device_type: str | None = None,
    ) -> DeterministicParser:
        selection = _Selection(
            kind, data_schema, vendor, platform, os_family, parser_profile
        )
        self._validate_selection(selection, device_type)
        entry = next(
            (item for item in self._entries if kind in item["observation_kinds"]),
            None,
        )
        parser = None if entry is None else self._parsers.get(entry["parser_id"])
        if parser is None or parser.parser_version != entry["parser_version"]:
            raise ParserError("Unsupported parser selection or parser version")
        return parser

    def resolve_input(self, parser_input: ParserInput) -> DeterministicParser:
        if not parser_input.observations:
            raise ParserError("Parser input must contain an observation")
        selections = [
            self._selection_key(item, parser_input.asset_context)
            for item in parser_input.observations
        ]
        if any(item != selections[0] for item in selections[1:]):
            raise ParserError("One parser input cannot mix parser selections")
        return self.resolve(**selections[0])

    def _validate_selection(
        self, selection: _Selection, device_type: str | None
    ) -> None:
        values = (
            selection.kind,
            selection.data_schema,
            selection.vendor,
            selection.platform,
            selection.os_family,
        )
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ParserError("Parser selection fields must be non-empty strings")
        if self._schemas.get(selection.kind) != selection.data_schema:
            raise ParserError("Unsupported parser selection: kind/schema mismatch")
        if selection.kind.startswith("vendor.") and not selection.parser_profile:
            raise ParserError("Vendor parser selections require parser_profile")
        if (
            not selection.kind.startswith("vendor.")
            and selection.parser_profile is not None
        ):
            raise ParserError(
                "Standard structured parsers do not accept parser_profile"
            )
        self._validate_capability(selection, device_type)

    @staticmethod
    def _validate_capability(selection: _Selection, device_type: str | None) -> None:
        if not device_type:
            registry = load_registry(
                "aioe.network-topology.registry/capability-profiles/v1"
            )
            matches = [
                item
                for item in registry["profiles"]
                if all(
                    item["match"][field] == "*"
                    or item["match"][field].lower() == getattr(selection, field).lower()
                    for field in ("vendor", "platform", "os_family")
                )
            ]
            if matches:
                return
            raise ParserError("Unsupported parser selection: device platform")
        try:
            resolve_capability(
                device_type,
                selection.vendor,
                selection.platform,
                selection.os_family,
            )
        except ContractError as exc:
            raise ParserError("Unsupported parser selection: device platform") from exc

    @staticmethod
    def _selection_key(
        observation: dict[str, Any], asset_context: Any
    ) -> dict[str, Any]:
        context = observation.get("parser_context") or {}
        return {
            "kind": observation.get("kind"),
            "data_schema": observation.get("data_schema"),
            "vendor": context.get("vendor") or asset_context.get("vendor"),
            "platform": context.get("platform") or asset_context.get("platform"),
            "os_family": context.get("os_family") or asset_context.get("os_family"),
            "parser_profile": context.get("parser_profile"),
            "device_type": asset_context.get("device_type"),
        }
