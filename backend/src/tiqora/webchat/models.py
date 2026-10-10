"""Typed shapes of the JSON columns of ``tiqora_chat_config``."""

from __future__ import annotations

from typing import Final, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tiqora.api.uploads import MAX_ATTACHMENT_BYTES
from tiqora.webchat.origins import validate_origins

MIB: Final = 1024 * 1024
ALLOWED_UPLOAD_TYPES: Final[tuple[str, ...]] = (
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "application/pdf",
    "text/plain",
)
UploadType = Literal[
    "image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf", "text/plain"
]
TOPIC_KEY_PATTERN: Final = r"^[a-z][a-z0-9_-]{0,39}$"
FIELD_KEY_PATTERN: Final = r"^[a-z][a-z0-9_]{0,39}$"
RESERVED_FIELD_KEYS: Final = frozenset({"message", "topic"})
DF_KINDS_FOR_PRECHAT: Final = frozenset({"Text", "TextArea", "Dropdown", "Checkbox"})


class ChatTopic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=TOPIC_KEY_PATTERN)
    label: str = Field(min_length=1, max_length=80)
    queue_id: int = Field(ge=1)
    priority_id: int | None = Field(default=None, ge=1)


class PrechatOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=100)


class PrechatField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=FIELD_KEY_PATTERN)
    kind: Literal["text", "textarea", "email", "select", "checkbox"]
    label: str = Field(min_length=1, max_length=100)
    required: bool = False
    options: list[PrechatOption] = Field(default_factory=list, max_length=50)
    maps_to: Literal["name", "email", "dynamic_field", "none"] = "none"
    dynamic_field: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _check(self) -> PrechatField:
        if self.key in RESERVED_FIELD_KEYS:
            raise ValueError(f"field key '{self.key}' is reserved")
        if (self.kind == "select") != bool(self.options):
            raise ValueError("options are required for select fields and only allowed there")
        values = [o.value for o in self.options]
        if len(values) != len(set(values)):
            raise ValueError("option values must be unique")
        if self.maps_to == "email" and self.kind != "email":
            raise ValueError("a field mapped to email must have kind email")
        if self.maps_to == "name" and self.kind != "text":
            raise ValueError("a field mapped to name must have kind text")
        if (self.maps_to == "dynamic_field") != bool(self.dynamic_field):
            raise ValueError("dynamic_field is required exactly when maps_to is dynamic_field")
        return self


class ChatTheme(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_color: str = Field(default="#2563eb", pattern=r"^#[0-9a-fA-F]{6}$")
    position: Literal["right", "left"] = "right"
    mode: Literal["auto", "light", "dark"] = "auto"
    launcher_label: str | None = Field(default=None, max_length=40)


def _default_upload_types() -> list[UploadType]:
    return list(get_args(UploadType))


class UploadPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_bytes: int = Field(default=10 * MIB, ge=MIB, le=MAX_ATTACHMENT_BYTES)
    types: list[UploadType] = Field(default_factory=_default_upload_types, min_length=1)


class ChatRateLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessions_per_ip_10min: int = Field(default=5, ge=1, le=100)
    sessions_per_config_minute: int = Field(default=30, ge=5, le=300)
    messages_per_session_minute: int = Field(default=30, ge=5, le=300)
    messages_per_ip_minute: int = Field(default=60, ge=5, le=600)
    uploads_per_session_10min: int = Field(default=10, ge=1, le=100)
    offline_per_ip_hour: int = Field(default=3, ge=1, le=50)
    offline_per_config_hour: int = Field(default=60, ge=1, le=1000)


def default_prechat_fields() -> list[PrechatField]:
    return [
        PrechatField(key="name", kind="text", label="Name", required=False, maps_to="name"),
        PrechatField(key="email", kind="email", label="E-Mail", required=False, maps_to="email"),
    ]


class ChatConfigValues(BaseModel):
    """Everything the admin edits except name / public_key / valid."""

    model_config = ConfigDict(extra="forbid")

    allowed_origins: list[str] = Field(default_factory=list, max_length=20)
    topics: list[ChatTopic] = Field(min_length=1, max_length=20)
    greeting: str | None = Field(default=None, max_length=500)
    privacy_url: str | None = Field(default=None, max_length=500)
    offline_mode: Literal["form", "hide"] = "form"
    business_calendar: str | None = Field(default=None, pattern=r"^(|[1-9][0-9]?)$")
    wait_timeout_seconds: int = Field(default=180, ge=30, le=3600)
    prechat_fields: list[PrechatField] = Field(
        default_factory=default_prechat_fields, max_length=15
    )
    upload_policy: UploadPolicy = Field(default_factory=lambda: UploadPolicy())
    theme: ChatTheme = Field(default_factory=lambda: ChatTheme())
    rate_limits: ChatRateLimits = Field(default_factory=lambda: ChatRateLimits())
    default_customer_user: str | None = Field(default=None, max_length=200)
    show_agent_names: bool = True
    retention_days: int = Field(default=30, ge=1, le=3650)

    @field_validator("allowed_origins")
    @classmethod
    def _origins(cls, value: list[str]) -> list[str]:
        return validate_origins(value)

    @field_validator("privacy_url")
    @classmethod
    def _privacy_url(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if value.startswith("https://") or value.startswith(
            ("http://localhost", "http://127.0.0.1")
        ):
            return value
        raise ValueError("privacy_url must start with https://")

    @model_validator(mode="after")
    def _check(self) -> ChatConfigValues:
        keys = [t.key for t in self.topics]
        if len(keys) != len(set(keys)):
            raise ValueError("topic keys must be unique")
        field_keys = [f.key for f in self.prechat_fields]
        if len(field_keys) != len(set(field_keys)):
            raise ValueError("prechat field keys must be unique")
        for target in ("name", "email"):
            if sum(1 for f in self.prechat_fields if f.maps_to == target) > 1:
                raise ValueError(f"at most one prechat field may map to {target}")
        return self
