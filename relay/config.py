import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


def read_env(path):
    result = {}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator or not key.strip():
            raise ValueError('配置文件行必须为KEY=VALUE')
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key.strip()] = value
    return result


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    center_url: str
    center_key: str = field(repr=False)
    device_key: str = field(repr=False)
    admin_key: str = field(repr=False)
    relay_id: str = 'workshop-1'
    host: str = '0.0.0.0'
    port: int = 18081
    poll_seconds: float = 10
    connect_timeout: float = 5
    read_timeout: float = 20
    download_timeout: float = 180
    retry_count: int = 3
    retry_max_seconds: float = 60
    reserve_bytes: int = 512 * 1024 * 1024
    cache_retention_hours: float = 24
    max_file_bytes: int = 2 * 1024**3
    max_json_bytes: int = 32 * 1024**2
    max_outbox_events: int = 100000
    max_local_downloads: int = 4
    auth_session_hours: int = 12
    auth_cookie_secure: bool = False
    heartbeat_seconds: int = 10
    offline_seconds: int = 40

    def __post_init__(self):
        object.__setattr__(self, 'data_dir', Path(self.data_dir).resolve())
        url = urlsplit(self.center_url)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('CENTER_URL必须为不含密钥、用户名或查询参数的HTTP(S)地址')
        keys = [self.center_key, self.device_key, self.admin_key]
        if len(set(keys)) != 3 or any(len(k) < 24 or not k.isascii() or not k.isprintable()
                                    or any(c.isspace() for c in k) or k.startswith('replace-') for k in keys):
            raise ValueError('三个API Key必须不同、至少24位ASCII、无空白，并替换示例值')
        if not self.relay_id or len(self.relay_id) > 128:
            raise ValueError('RELAY_ID无效')
        if not 1 <= self.port <= 65535 or not 1 <= self.retry_count <= 5:
            raise ValueError('端口或重试次数无效')
        if min(self.poll_seconds, self.connect_timeout, self.read_timeout, self.download_timeout,
               self.retry_max_seconds, self.max_file_bytes, self.max_json_bytes, self.max_outbox_events,
               self.heartbeat_seconds, self.offline_seconds, self.max_local_downloads) <= 0 or self.reserve_bytes < 0 or self.cache_retention_hours < 0:
            raise ValueError('时间、大小上限必须为正数，保留空间和缓存时间不得为负')
        if self.offline_seconds <= self.heartbeat_seconds:
            raise ValueError('离线阈值必须大于心跳周期')
        if not 1 <= self.auth_session_hours <= 168:raise ValueError('AUTH_SESSION_HOURS必须为1–168')

    @classmethod
    def from_env(cls, path=None):
        values = {**(read_env(path) if path else {}), **os.environ}
        def get(name, default): return values.get(name, str(default))
        return cls(data_dir=Path(values['RELAY_DATA_DIR']), center_url=values['CENTER_URL'].rstrip('/'),
            center_key=values['CENTER_API_KEY'], device_key=values['DEVICE_API_KEY'], admin_key=values['LOCAL_ADMIN_API_KEY'],
            relay_id=get('RELAY_ID', 'workshop-1'), host=get('RELAY_HOST', '0.0.0.0'), port=int(get('RELAY_PORT', 18081)),
            poll_seconds=float(get('POLL_SECONDS', 10)), connect_timeout=float(get('CONNECT_TIMEOUT_SECONDS', 5)),
            read_timeout=float(get('READ_TIMEOUT_SECONDS', 20)), download_timeout=float(get('DOWNLOAD_TIMEOUT_SECONDS', 180)),
            retry_count=int(get('DOWNLOAD_RETRIES', 3)), retry_max_seconds=float(get('RETRY_MAX_SECONDS', 60)),
            reserve_bytes=int(get('DISK_RESERVE_MB', 512))*1024**2,
            cache_retention_hours=float(get('CACHE_RETENTION_HOURS', 24)),
            max_file_bytes=int(get('MAX_FILE_MB', 2048))*1024**2,
            max_json_bytes=int(get('MAX_JSON_MB', 32))*1024**2,
            max_outbox_events=int(get('MAX_OUTBOX_EVENTS', 100000)),
            max_local_downloads=int(get('MAX_LOCAL_DOWNLOADS', 4)),
            auth_session_hours=int(get('AUTH_SESSION_HOURS',12)),
            auth_cookie_secure=get('AUTH_COOKIE_SECURE','false').lower()=='true')
