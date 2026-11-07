import hashlib
import re
from datetime import datetime
from typing import Literal

from pydantic import Field
from shared.reports import DeviceReport, Input
from .db import dumps


ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')


class RelayError(Exception):
    def __init__(self, message, status=409, code='conflict'):
        super().__init__(message)
        self.status, self.code = status, code


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise RelayError('协议ID无效', 422, 'validation_error')
    return value


def utc(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError('时间必须为UTC')


class File(Input):
    id: str
    download_path: str
    media_type: Literal['image/png', 'image/jpeg', 'video/mp4', 'application/pdf']
    size_bytes: int = Field(gt=0, strict=True)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    role: Literal['page', 'image', 'video', 'pdf']
    page_number: int | None = None


class Item(Input):
    id: str
    position: int = Field(ge=0, strict=True)
    type: Literal['page', 'image', 'video']
    duration_seconds: float | None
    playback: Literal['timed', 'once']
    file: File
    category: Literal['production', 'inspection', 'check_video', 'custom'] | None = None
    asset_id: str | None = None


class Module(Input):
    id: Literal['production', 'inspection', 'check_video', 'custom']
    label: str
    items: list[Item]
    loop: Literal[True]


class Display(Input):
    width: Literal[1920]
    height: Literal[1080]
    aspect_ratio: Literal['16:9']
    fit: Literal['contain']


class Manifest(Input):
    schema_version: Literal[1, 2]
    playlist_version_id: str
    name: str
    version: int = Field(ge=1, strict=True)
    published_at: str
    loop: Literal[True]
    items: list[Item] = Field(min_length=1, max_length=2000)
    modules: list[Module] | None = None
    display: Display | None = None


class Assignment(Input):
    device_id: str
    assignment_revision: int = Field(ge=1, strict=True)
    playlist_version_id: str
    task_id: str
    assigned_at: str
    relay_acked_at: str | None = None


class SyncInput(Input):
    device_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    known_assignment_revision: int = Field(ge=0, strict=True)


class ActivationInput(Input):
    device_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    assignment_revision: int = Field(ge=1, strict=True)
    playlist_version_id: str


class ReportInput(Input):
    report: DeviceReport


def validate_version(descriptor, settings):
    version_id = identifier(descriptor['id'])
    raw = descriptor['manifest']
    manifest = Manifest.model_validate(raw)
    if manifest.playlist_version_id != version_id:
        raise RelayError('清单版本ID不一致')
    utc(manifest.published_at)
    digest = hashlib.sha256(dumps(raw).encode('utf-8')).hexdigest()
    if digest != descriptor['manifest_sha256']:
        raise RelayError('manifest SHA-256校验失败', 422, 'checksum_mismatch')
    files = {}
    if not 1 <= len(descriptor['files']) <= 4000:
        raise RelayError('文件数量超出版本限制', 422, 'validation_error')
    for raw_file in descriptor['files']:
        file = File.model_validate(raw_file)
        identifier(file.id)
        if file.download_path != '/api/v1/files/'+file.id or file.size_bytes > settings.max_file_bytes:
            raise RelayError('文件路径或大小超出契约限制', 422, 'validation_error')
        if file.id in files and files[file.id] != raw_file:
            raise RelayError('同一文件ID有不同描述')
        files[file.id] = raw_file
    ids = set()
    for position, (item, raw_item) in enumerate(zip(manifest.items, raw['items'])):
        identifier(item.id)
        if item.id in ids or item.position != position or item.file.role != item.type:
            raise RelayError('播放项顺序、ID或类型无效', 422, 'validation_error')
        ids.add(item.id)
        if item.file.id not in files or files[item.file.id] != raw_item['file']:
            raise RelayError('播放项文件不在完整文件清单内')
        expected = {'page': 'image/png', 'image': item.file.media_type, 'video': 'video/mp4'}[item.type]
        if item.file.media_type != expected or (item.type == 'image' and not expected.startswith('image/')):
            raise RelayError('播放项媒体类型不符')
        if item.type == 'video':
            if item.playback != 'once' or item.duration_seconds is not None:
                raise RelayError('视频必须完整播放一次')
        elif item.playback != 'timed' or item.duration_seconds is None or not 1 <= item.duration_seconds <= 3600:
            raise RelayError('图片停留时间无效')
    if manifest.schema_version == 2:
        expected_ids = ['production', 'inspection', 'check_video', 'custom']
        if manifest.display is None or manifest.modules is None or [m.id for m in manifest.modules] != expected_ids:
            raise RelayError('schema2必须包含四模块和16:9显示规则')
        for module, raw_module in zip(manifest.modules, raw['modules']):
            if raw_module['items'] != [i for i in raw['items'] if i.get('category', 'custom') == module.id]:
                raise RelayError('模块播放项与根清单不一致')
    return version_id, raw, digest, list(files.values())
