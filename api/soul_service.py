"""Soul template HTTP API.

This is intentionally outside ``contracts/``: soul templates are runtime prompt
assets, not frozen cross-team schemas.
"""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

_ROOT = Path(__file__).resolve().parents[1]
_BUILTIN_SOUL_DIR = _ROOT / "agent_policy" / "prompts" / "souls"
_SOUL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_SOUL_CHARS = 8_000
_DANGEROUS_PATTERNS = (
    "忽略 allowed_actions",
    "忽略allowed_actions",
    "无视 allowed_actions",
    "读取真实身份",
    "真实身份",
    "隐藏身份",
    "泄露身份",
    "突破信息边界",
    "忽略规则",
    "ignore allowed_actions",
    "ignore the rules",
    "reveal hidden role",
    "read truthstate",
    "read truth state",
)

router = APIRouter()


class SoulTemplate(BaseModel):
    id: str
    name: str
    source: Literal["builtin", "custom"]
    summary: str


class ListSoulsResponse(BaseModel):
    souls: list[SoulTemplate]


class CreateSoulRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    content: str = Field(max_length=_MAX_SOUL_CHARS)
    soul_id: str | None = Field(default=None, min_length=1, max_length=64)


class SoulResponse(BaseModel):
    soul: SoulTemplate


@dataclass(frozen=True)
class SoulRecord:
    id: str
    name: str
    source: Literal["builtin", "custom"]
    summary: str
    path: Path

    def to_template(self) -> SoulTemplate:
        return SoulTemplate(
            id=self.id,
            name=self.name,
            source=self.source,
            summary=self.summary,
        )


class SoulLibrary:
    def __init__(
        self,
        *,
        builtin_dir: Path | None = None,
        custom_dir: Path | None = None,
    ) -> None:
        self._builtin_dir = builtin_dir or _BUILTIN_SOUL_DIR
        self._custom_dir = custom_dir or _default_custom_soul_dir()

    @property
    def custom_dir(self) -> Path:
        return self._custom_dir

    def list(self) -> list[SoulRecord]:
        records: list[SoulRecord] = []
        records.extend(self._read_dir(self._builtin_dir, "builtin"))
        records.extend(self._read_dir(self._custom_dir, "custom"))
        return sorted(records, key=lambda item: (item.source != "builtin", item.id))

    def exists(self, soul_id: str) -> bool:
        return any(record.id == soul_id for record in self.list())

    def create(self, request: CreateSoulRequest) -> SoulRecord:
        name = request.name.strip()
        content = request.content.strip()
        soul_id = request.soul_id.strip() if request.soul_id else self._generate_id(name)
        _validate_soul_id(soul_id)
        _validate_custom_soul(name=name, content=content)
        if self.exists(soul_id):
            raise SoulConflictError(f"soul already exists: {soul_id}")

        self._custom_dir.mkdir(parents=True, exist_ok=True)
        path = self._custom_dir / f"{soul_id}.md"
        path.write_text(_format_custom_soul(name, content), encoding="utf-8")
        return _parse_soul_file(path, "custom")

    def delete_custom(self, soul_id: str) -> None:
        _validate_soul_id(soul_id)
        if (self._builtin_dir / f"{soul_id}.md").is_file():
            raise SoulValidationError("builtin soul templates cannot be deleted")
        path = self._custom_dir / f"{soul_id}.md"
        if not path.is_file():
            raise SoulNotFoundError(f"soul not found: {soul_id}")
        path.unlink()

    def _generate_id(self, name: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip().lower()).strip("_")
        slug = slug[:40] or "custom_soul"
        candidate = slug
        if not self.exists(candidate):
            return candidate
        for _ in range(20):
            candidate = f"{slug}_{secrets.token_hex(3)}"
            if not self.exists(candidate):
                return candidate
        raise SoulConflictError("could not allocate unique soul id")

    @staticmethod
    def _read_dir(root: Path, source: Literal["builtin", "custom"]) -> list[SoulRecord]:
        if not root.is_dir():
            return []
        return [
            _parse_soul_file(path, source)
            for path in sorted(root.glob("*.md"))
            if _SOUL_ID_RE.fullmatch(path.stem)
        ]


class SoulValidationError(ValueError):
    pass


class SoulConflictError(ValueError):
    pass


class SoulNotFoundError(ValueError):
    pass


def get_soul_library() -> SoulLibrary:
    return SoulLibrary()


@router.get("/souls", response_model=ListSoulsResponse)
def list_souls(library: SoulLibrary = Depends(get_soul_library)) -> ListSoulsResponse:
    return ListSoulsResponse(souls=[record.to_template() for record in library.list()])


@router.post("/souls", response_model=SoulResponse)
def create_soul(
    request: CreateSoulRequest,
    library: SoulLibrary = Depends(get_soul_library),
) -> SoulResponse:
    try:
        return SoulResponse(soul=library.create(request).to_template())
    except SoulConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SoulValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/souls/{soul_id}")
def delete_soul(
    soul_id: str,
    library: SoulLibrary = Depends(get_soul_library),
) -> dict[str, str]:
    try:
        library.delete_custom(soul_id)
    except SoulNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SoulValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "deleted"}


def _default_custom_soul_dir() -> Path:
    return Path(os.getenv("AI_WOLF_DATA_DIR", "./data")) / "souls"


def _validate_soul_id(soul_id: str) -> None:
    if not _SOUL_ID_RE.fullmatch(soul_id):
        raise SoulValidationError(
            "soul_id must contain only letters, numbers, underscores, or hyphens"
        )


def _validate_custom_soul(*, name: str, content: str) -> None:
    if not name:
        raise SoulValidationError("name cannot be empty")
    if not content:
        raise SoulValidationError("content cannot be empty")
    if len(content) > _MAX_SOUL_CHARS:
        raise SoulValidationError(f"content exceeds {_MAX_SOUL_CHARS} characters")
    lowered = content.lower()
    for pattern in _DANGEROUS_PATTERNS:
        if pattern.lower() in lowered:
            raise SoulValidationError("content contains unsafe role or rule override instructions")


def _format_custom_soul(name: str, content: str) -> str:
    body = content.strip()
    if not body.startswith("#"):
        body = f"# Soul：{name.strip()}\n\n{body}"
    return body + "\n"


def _parse_soul_file(path: Path, source: Literal["builtin", "custom"]) -> SoulRecord:
    text = path.read_text(encoding="utf-8").strip()
    lines = [line.strip() for line in text.splitlines()]
    title = next((line.lstrip("#").strip() for line in lines if line.startswith("#")), path.stem)
    title = title.replace("Soul：", "").replace("Soul:", "").strip() or path.stem
    summary = _first_summary_line(lines)
    return SoulRecord(
        id=path.stem,
        name=title,
        source=source,
        summary=summary,
        path=path,
    )


def _first_summary_line(lines: list[str]) -> str:
    for line in lines:
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        return line[:120]
    return "自定义发言风格与软倾向模板。"
