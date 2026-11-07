from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class DownloadProgress(Input):
    playlist_version_id: str = Field(min_length=1,max_length=128)
    received_bytes: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    completed_files: int = Field(ge=0)
    total_files: int = Field(ge=1,le=2000)

    @model_validator(mode='after')
    def within_totals(self):
        if self.received_bytes>self.total_bytes or self.completed_files>self.total_files:
            raise ValueError('下载进度不得超过总数')
        return self


class DeviceReport(Input):
    event_id: str = Field(min_length=1, max_length=128)
    device_id: str
    report_seq: int = Field(ge=1)
    observed_at: datetime
    assignment_revision: int = Field(ge=0)
    actual_version_id: str | None = None
    current_item_id: str | None = None
    playback_state: Literal['idle', 'downloading', 'playing', 'paused', 'error']
    pending_version_id: str | None = None
    error: str | None = Field(default=None, max_length=2000)
    download_progress: DownloadProgress | None = None

    @field_validator('observed_at')
    @classmethod
    def utc_only(cls, value):
        if value.tzinfo is None or value.utcoffset().total_seconds() != 0:
            raise ValueError('observed_at 必须为 UTC 时间（Z / +00:00）')
        if (value - datetime.now(timezone.utc)).total_seconds() > 30:
            raise ValueError('回报时间超过中心30秒，请校时')
        return value


def report_data(report):
    data=report.model_dump(mode='json')
    if data.get('download_progress') is None:data.pop('download_progress',None)
    return data
