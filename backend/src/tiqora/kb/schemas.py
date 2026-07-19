"""Pydantic v2 request/response models for the KB REST + portal + MCP surfaces."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from tiqora.kb.models import STATE_DRAFT


class CategoryIn(BaseModel):
    parent_id: int | None = None
    name: str
    slug: str
    permission_group_id: int | None = None
    customer_visible: bool = False
    sort: int = 0
    valid: bool = True


class CategoryUpdateIn(BaseModel):
    parent_id: int | None = None
    name: str | None = None
    slug: str | None = None
    permission_group_id: int | None = None
    customer_visible: bool | None = None
    sort: int | None = None
    valid: bool | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_id: int | None
    name: str
    slug: str
    permission_group_id: int | None
    customer_visible: bool
    sort: int
    valid: bool
    create_time: datetime
    change_time: datetime


class ArticleIn(BaseModel):
    category_id: int
    title: str
    slug: str
    language: str = "en"
    content_md: str
    tags: list[str] = Field(default_factory=list)


class ArticleUpdateIn(BaseModel):
    category_id: int | None = None
    title: str | None = None
    content_md: str | None = None
    language: str | None = None
    state: str | None = None
    tags: list[str] | None = None


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


class ArticleVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    article_id: int
    version: int
    title: str
    content_md: str
    changed_by: int
    changed_at: datetime


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
    permission_group_id: int | None = None
    score: float | None = None


class KbSearchResponse(BaseModel):
    query: str
    hits: list[KbSearchHit]
    estimated_total: int
