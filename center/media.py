import hashlib
import json
import math
import os
import signal
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image, UnidentifiedImageError

from .db import uid


class MediaError(Exception):
    def __init__(self, message, status=422, code=None, details=None):
        super().__init__(message)
        self.status = status
        self.code, self.details = code, details


def run_command(args, timeout):
    """No shell; kill the whole Linux process group, including soffice children, on timeout."""
    try:
        p = subprocess.Popen([str(a) for a in args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=(os.name != "nt"))
    except FileNotFoundError as e:
        raise MediaError(f"缺少转换/检测依赖：{Path(str(args[0])).name}", 503) from e
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        if os.name == "nt":
            p.kill()
        else:
            os.killpg(p.pid, signal.SIGKILL)
        p.communicate()
        raise MediaError(f"媒体处理超时（{timeout}秒）") from e
    if p.returncode:
        detail = err.decode("utf-8", errors="replace")[-1500:]
        raise MediaError(f"媒体处理失败：{detail or '命令退出 ' + str(p.returncode)}")
    return out


def file_record(path, root, asset_id, role, media_type, page_number=None):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return dict(id=uid("file"), asset_id=asset_id, path=path.relative_to(root).as_posix(),
                role=role, media_type=media_type, size_bytes=path.stat().st_size,
                sha256=digest.hexdigest(), page_number=page_number)


def insert_file(c, record):
    keys = list(record)
    c.execute(f"INSERT INTO files({','.join(keys)}) VALUES({','.join('?' for _ in keys)})", list(record.values()))


def public_file(row):
    return {k: row[k] for k in ("id", "media_type", "size_bytes", "sha256", "role", "page_number")} | {
        "download_path": "/api/v1/files/" + row["id"]}


def verify_image(path):
    Image.MAX_IMAGE_PIXELS = 40_000_000
    try:
        with Image.open(path) as image:
            if image.format not in ("PNG", "JPEG"):
                raise MediaError("只支持真实 PNG / JPEG 图片", 415)
            fmt = image.format
            width, height = image.size
            if width * height > Image.MAX_IMAGE_PIXELS:
                raise MediaError("图片像素超过4000万上限")
            image.verify()
        # Decode as well as verifying the container, catching truncated image data.
        with Image.open(path) as image:
            image.load()
        return {"width": width, "height": height}, "image/png" if fmt == "PNG" else "image/jpeg"
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as e:
        raise MediaError("无效、损坏或像素过大的图片") from e


def verify_video(path, settings):
    data = json.loads(run_command([settings.ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe", "-show_format", "-show_streams",
                                  "-of", "json", path], 30))
    streams = data.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s.get("codec_name") for s in streams if s.get("codec_type") == "audio"]
    formats = data.get("format", {}).get("format_name", "").split(",")
    if "mp4" not in formats or len(videos) != 1 or videos[0].get("codec_name") != "h264" or any(a != "aac" for a in audio):
        raise MediaError("要求 MP4 容器、单 H.264 视频轨和 AAC 音轨（或无音轨）；请外部转码后上传，中心暂不转码", 415)
    if any(s.get("codec_type") not in ("video", "audio") for s in streams):
        raise MediaError("请移除 MP4 的字幕/数据轨后上传", 415)
    video = videos[0]
    if video.get("pix_fmt") != "yuv420p" or video.get("disposition", {}).get("attached_pic"):
        raise MediaError("H.264 视频要求 yuv420p 像素格式，不能是封面图轨；请外部转换", 415)
    try:
        durations = [float(data["format"]["duration"])]
        durations += [float(s["duration"]) for s in streams if s.get("duration") is not None]
        duration = max(durations)
    except (KeyError, TypeError, ValueError) as e:
        raise MediaError("无法确认视频真实时长") from e
    if any(not math.isfinite(d) or d <= 0 for d in durations):
        raise MediaError("视频时长无效")
    if duration > settings.video_max_seconds:
        raise MediaError(f"视频时长 {duration:.3f}秒超过上限 {settings.video_max_seconds:g}秒，不自动截断")
    width, height = video.get("width", 0), video.get("height", 0)
    if width <= 0 or height <= 0:
        raise MediaError("视频分辨率无效")
    return dict(duration_seconds=duration, width=width, height=height, video_codec="h264", audio_codecs=audio)


def excel_warnings(path):
    warnings = ["请逐页核对字体、表格、嵌入图片和内容完整性；替代字体可能改变原 Excel 版式。"]
    if path.suffix.lower() == ".xls":
        with path.open("rb") as stream:
            signature = stream.read(8)
        if signature != bytes.fromhex("d0cf11e0a1b11ae1"):
            raise MediaError("不是有效的 XLS 二进制工作簿")
        warnings.append("XLS 打印区域无法预检，请在 PDF 预览中人工检查分页与方向。")
        return warnings
    try:
        with zipfile.ZipFile(path) as z:
            if sum(i.file_size for i in z.infolist()) > 200 * 1024 * 1024:
                raise MediaError("Excel 解压体积超过200MB")
            ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            workbook = ET.fromstring(z.read("xl/workbook.xml"))
            areas = {int(n.attrib.get("localSheetId", "-1")) for n in workbook.findall("s:definedNames/s:definedName", ns)
                     if n.attrib.get("name") == "_xlnm.Print_Area" and n.text}
            sheets = workbook.findall("s:sheets/s:sheet", ns)
            missing = [s.attrib.get("name", "?") for i, s in enumerate(sheets)
                       if i not in areas and s.attrib.get("state", "visible") == "visible"]
            if missing:
                warnings.append("未设置打印区域：" + "、".join(missing) + "；可能出现多余分页，请检查预览。")
            for name in z.namelist():
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"):
                    sheet = ET.fromstring(z.read(name))
                    setup = sheet.find("s:pageSetup", ns)
                    if setup is not None and setup.get("fitToHeight") == "1":
                        warnings.append("原文件含单页高度缩放设置；长表可能字太小，请检查展示可读性。")
            if any("externalLink" in name or name == "xl/connections.xml" for name in z.namelist()):
                warnings.append("含外部链接/数据连接；转换不刷新外部数据，仅使用文件内保存内容。")
            if 'xl/cellimages.xml' in z.namelist():
                warnings.append('检测到WPS单元格图片，将进行兼容转换；请核对分页预览中的图片。')
    except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError) as e:
        raise MediaError("损坏或不是有效的 XLSX 工作簿") from e
    return list(dict.fromkeys(warnings))
