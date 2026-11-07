from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .categories import Category
from shared.reports import Input, DeviceReport


class NamedInput(Input):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()


class PlaylistCreate(NamedInput):
    description: str = Field(default='', max_length=1000)


class DeviceCreate(NamedInput):
    device_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class ItemInput(Input):
    asset_id: str | None = None
    file_id: str | None = None
    duration_seconds: float | None = Field(default=None, ge=1, le=3600)
    category: Category | None = None

    @model_validator(mode="after")
    def exactly_one(self):
        if bool(self.asset_id) == bool(self.file_id):
            raise ValueError("指定 asset_id 或 file_id，二者选一")
        return self


class ItemsInput(Input):
    items: list[ItemInput] = Field(max_length=2000)


class DeploymentCreate(Input):
    request_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    playlist_version_id: str
    device_ids: list[str] = Field(min_length=1, max_length=1000)
    timeout_seconds: int = Field(default=120, ge=10, le=86400)

    @field_validator("device_ids")
    @classmethod
    def unique_devices(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("device_ids 不可重复")
        return sorted(value)


class RelayInput(Input):
    relay_id: str


class AssignmentAck(Input):
    device_id: str
    assignment_revision: int = Field(ge=1)


class AcksInput(RelayInput):
    assignments: list[AssignmentAck] = Field(max_length=1000)


class ReportsInput(RelayInput):
    reports: list[DeviceReport] = Field(min_length=1, max_length=500)
