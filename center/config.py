import os
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    admin_key: str
    relay_key: str
    relay_id: str = "workshop-1"
    heartbeat_seconds: int = 10
    offline_seconds: int = 40
    upload_max_bytes: int = 100 * 1024 * 1024
    video_max_bytes: int = 200 * 1024 * 1024
    video_max_seconds: float = 180
    conversion_timeout_seconds: int = 120
    max_pages: int = 200
    page_warning_count: int = 30
    render_dpi: int = 160
    uno_python: str = "/usr/bin/python3"
    soffice: str = "libreoffice"
    ffprobe: str = "ffprobe"
    pdfinfo: str = "pdfinfo"
    pdftoppm: str = "pdftoppm"
    auth_session_hours: int = 12
    auth_cookie_secure: bool = False

    def __post_init__(self):
        if min(len(self.admin_key), len(self.relay_key)) < 24 or self.admin_key == self.relay_key:
            raise ValueError("ADMIN_API_KEY 和 RELAY_API_KEY 必须不同且至少24字符")
        if self.admin_key.startswith("replace-") or self.relay_key.startswith("replace-"):
            raise ValueError("请替换 .env.example 中的示例密钥")
        if any(not key.isascii() or not key.isprintable() or any(ch.isspace() for ch in key)
               for key in (self.admin_key, self.relay_key)):
            raise ValueError("API Key 必须为不含空白的可打印 ASCII 字符")
        if self.heartbeat_seconds < 1 or self.offline_seconds <= self.heartbeat_seconds:
            raise ValueError("离线阈值必须大于心跳周期")
        if not math.isfinite(self.video_max_seconds) or min(self.upload_max_bytes, self.video_max_bytes, self.video_max_seconds,
               self.conversion_timeout_seconds, self.max_pages, self.page_warning_count) <= 0:
            raise ValueError("上限配置必须为正数")
        if not 72 <= self.render_dpi <= 300:
            raise ValueError("RENDER_DPI 必须为72–300")
        if not 1 <= self.auth_session_hours <= 168:
            raise ValueError('AUTH_SESSION_HOURS必须为1–168')

    @classmethod
    def from_env(cls):
        return cls(
            data_dir=Path(os.getenv("DATA_DIR", "data")).resolve(),
            admin_key=os.getenv("ADMIN_API_KEY", ""), relay_key=os.getenv("RELAY_API_KEY", ""),
            relay_id=os.getenv("RELAY_ID", "workshop-1"),
            heartbeat_seconds=int(os.getenv("HEARTBEAT_SECONDS", "10")),
            offline_seconds=int(os.getenv("OFFLINE_SECONDS", "40")),
            upload_max_bytes=int(os.getenv("UPLOAD_MAX_MB", "100")) * 1024 * 1024,
            video_max_bytes=int(os.getenv("VIDEO_MAX_MB", "200")) * 1024 * 1024,
            video_max_seconds=float(os.getenv("VIDEO_MAX_SECONDS", "180")),
            conversion_timeout_seconds=int(os.getenv("CONVERSION_TIMEOUT_SECONDS", "120")),
            max_pages=int(os.getenv("MAX_PAGES", "200")),
            page_warning_count=int(os.getenv("PAGE_WARNING_COUNT", "30")),
            render_dpi=int(os.getenv("RENDER_DPI", "160")),
            uno_python=os.getenv("UNO_PYTHON", "/usr/bin/python3"),
            soffice=os.getenv("SOFFICE_BIN", "libreoffice"),
            ffprobe=os.getenv("FFPROBE_BIN", "ffprobe"),
            pdfinfo=os.getenv("PDFINFO_BIN", "pdfinfo"),
            pdftoppm=os.getenv("PDFTOPPM_BIN", "pdftoppm"),
            auth_session_hours=int(os.getenv('AUTH_SESSION_HOURS','12')),
            auth_cookie_secure=os.getenv('AUTH_COOKIE_SECURE','false').lower()=='true',
        )
