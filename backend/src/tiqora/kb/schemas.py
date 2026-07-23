"""Pydantic v2 request/response models for the KB REST + portal + MCP surfaces."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tiqora.kb.models import ARTICLE_STATES, STATE_DRAFT


def _validate_state(value: str | None) -> str | None:
    if value is not None and value not in ARTICLE_STATES:
        raise ValueError(f"state must be one of {sorted(ARTICLE_STATES)}")
    return value


class CategoryIn(BaseModel):
    parent_id: int | None = None
    name: str
    #: Optional — derived from ``name`` when omitted (see ``KbService``).
    slug: str | None = None
    #: Permission groups that may see this category's articles. Empty = every
    #: agent can see them. Authors may only set groups they hold ``rw`` on.
    permission_group_ids: list[int] = Field(default_factory=list)
    customer_visible: bool = False
    sort: int = 0
    valid: bool = True


class CategoryUpdateIn(BaseModel):
    parent_id: int | None = None
    name: str | None = None
    slug: str | None = None
    permission_group_ids: list[int] | None = None
    customer_visible: bool | None = None
    sort: int | None = None
    valid: bool | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_id: int | None
    name: str
    slug: str
    permission_group_ids: list[int] = Field(default_factory=list)
    customer_visible: bool
    sort: int
    valid: bool
    create_time: datetime
    change_time: datetime


class AssignableGroup(BaseModel):
    """A permission group the current user may assign to a KB category."""

    id: int
    name: str


class ArticleIn(BaseModel):
    category_id: int
    title: str
    #: Optional — derived from ``title`` when omitted (see ``KbService``).
    slug: str | None = None
    language: str = "en"
    #: Optional lifecycle state on create; defaults to ``draft``.
    state: str | None = None
    content_md: str
    tags: list[str] = Field(default_factory=list)

    _check_state = field_validator("state")(_validate_state)


class ArticleUpdateIn(BaseModel):
    category_id: int | None = None
    title: str | None = None
    content_md: str | None = None
    language: str | None = None
    state: str | None = None
    tags: list[str] | None = None

    _check_state = field_validator("state")(_validate_state)


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category_id: int
    title: str
    slug: str
    language: str
    state: str
    content_md: str
    version: int
    create_by: int
    create_time: datetime
    change_by: int
    change_time: datetime
    tags: list[str] = Field(default_factory=list)


class ArticleSummary(BaseModel):
    id: int
    category_id: int
    title: str
    slug: str
    language: str
    state: str
    version: int
    change_time: datetime
    tags: list[str] = Field(default_factory=list)


class ArticleVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    article_id: int
    version: int
    title: str
    content_md: str
    changed_by: int
    changed_at: datetime


class AttachmentOut(BaseModel):
    """Metadata for a KB article attachment (content fetched via download route)."""

    id: int
    article_id: int
    filename: str
    content_type: str | None = None
    size: int


class TagOut(BaseModel):
    """A KB tag with the count of articles visible to the requesting user."""

    name: str
    article_count: int


class KnowledgeArticle(BaseModel):
    """One article in a knowledge bundle handed to an agent/LLM."""

    id: int
    category_id: int
    title: str
    slug: str
    language: str
    state: str
    tags: list[str] = Field(default_factory=list)
    content_md: str | None = None


class KnowledgeBundle(BaseModel):
    """ACL-filtered set of articles selected by tag(s) and/or category."""

    tags: list[str] = Field(default_factory=list)
    category_id: int | None = None
    total: int
    articles: list[KnowledgeArticle]


class KbSearchHit(BaseModel):
    article_id: int
    chunk_id: int
    title: str
    heading_path: str
    anchor: str
    content: str
    language: str
    state: str = STATE_DRAFT
    customer_visible: bool = False
    #: Permission groups the source category is restricted to (empty = all).
    permission_group_ids: list[int] = Field(default_factory=list)
    score: float | None = None


class KbSearchResponse(BaseModel):
    query: str
    hits: list[KbSearchHit]
    estimated_total: int
