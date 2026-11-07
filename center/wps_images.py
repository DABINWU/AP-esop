"""Read WPS DISPIMG references without rewriting or resaving an XLSX package.

Only embedded PNG/JPEG and literal DISPIMG(id, 1) are supported. Missing/ambiguous
references fail the conversion instead of publishing a workbook with lost pictures.
This module also runs under the distro Python used by LibreOffice UNO (stdlib only).
"""
import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
P = 'http://schemas.openxmlformats.org/package/2006/relationships'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
XDR = 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing'
DISPIMG = re.compile(r'\s*=?\s*@?(?:_xlfn\.)?DISPIMG\(\s*"([^"\r\n]+)"\s*,\s*1\s*\)\s*', re.I)
HAS_DISPIMG = re.compile(r'(?<![A-Z0-9_])(?:_xlfn\.)?DISPIMG\s*\(', re.I)


class WpsImageError(ValueError):
    pass


@dataclass(frozen=True)
class CellImage:
    sheet_name: str
    cell: str
    bounds: str
    image_id: str
    package_path: str
    data: bytes


def part_path(base, target):
    """Resolve only local OOXML parts, never fetch a URL or leave the package."""
    if not target or '\\' in target or ':' in target or '?' in target or '#' in target:
        raise WpsImageError('WPS图片引用不是安全的文件内路径')
    path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else
                             posixpath.join(posixpath.dirname(base), target))
    if path == '..' or path.startswith('../') or not path.startswith('xl/'):
        raise WpsImageError('WPS图片引用超出工作簿范围')
    return path


def relationships(z, part):
    rel_part = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
    if rel_part not in z.namelist():
        return {}
    result = {}
    for rel in ET.fromstring(z.read(rel_part)).findall(f'{{{P}}}Relationship'):
        rid = rel.get('Id')
        if not rid or rid in result:
            raise WpsImageError('工作簿关系ID缺失或重复，无法可靠定位图片')
        result[rid] = rel
    return result


def coordinates(address):
    match = re.fullmatch(r'\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,6})', address or '')
    if not match:
        raise WpsImageError('WPS图片的单元格地址无效')
    column = 0
    for ch in match[1].upper():
        column = column * 26 + ord(ch) - ord('A') + 1
    row = int(match[2])
    if column > 16384 or row > 1048576:
        raise WpsImageError('WPS图片的单元格地址超出Excel范围')
    return column, row


def containing_range(cell, merges):
    col, row = coordinates(cell)
    for merged in merges:
        first, last = merged.split(':') if ':' in merged else (merged, merged)
        left, top = coordinates(first)
        right, bottom = coordinates(last)
        if left <= col <= right and top <= row <= bottom:
            return merged
    return cell


def load_cell_images(path):
    if Path(path).suffix.lower() != '.xlsx':
        return []
    try:
        with zipfile.ZipFile(path) as z:
            if sum(i.file_size for i in z.infolist()) > 200 * 1024 * 1024:
                raise WpsImageError('Excel解压体积超过200MB')
            names = set(z.namelist())
            workbook = ET.fromstring(z.read('xl/workbook.xml'))
            wb_rels = relationships(z, 'xl/workbook.xml')
            references = []
            for sheet in workbook.findall(f'{{{S}}}sheets/{{{S}}}sheet'):
                rel = wb_rels.get(sheet.get(f'{{{R}}}id'))
                if rel is None or rel.get('TargetMode') == 'External':
                    raise WpsImageError('工作表引用缺失或指向外部文件')
                if not (rel.get('Type') or '').endswith('/worksheet'):
                    continue  # Chart sheets contain no spreadsheet cells.
                sheet_part = part_path('xl/workbook.xml', rel.get('Target'))
                tree = ET.fromstring(z.read(sheet_part))
                merges = [m.get('ref') for m in tree.findall(f'{{{S}}}mergeCells/{{{S}}}mergeCell')]
                for cell in tree.findall(f'{{{S}}}sheetData/{{{S}}}row/{{{S}}}c'):
                    formula = cell.find(f'{{{S}}}f')
                    text = (formula.text or '') if formula is not None else ''
                    # A literal text string containing "DISPIMG(" is not an image function.
                    code = re.sub(r'"(?:[^"]|"")*"', '""', text)
                    if not HAS_DISPIMG.search(code):
                        continue
                    match = DISPIMG.fullmatch(text)
                    address = cell.get('r')
                    if not match:
                        raise WpsImageError(f'{sheet.get("name")}!{address} 的WPS图片公式不受支持；请改为普通浮动图片')
                    if formula.get('t') not in (None, 'normal'):
                        raise WpsImageError(f'{sheet.get("name")}!{address} 的共享/数组WPS图片公式暂不支持；请改为普通浮动图片')
                    references.append((sheet.get('name'), address, containing_range(address, merges), match[1]))
            if not references:
                return []
            if len(references) > 2000:
                raise WpsImageError('WPS单元格图片超过2000张上限')
            part = 'xl/cellimages.xml'
            if part not in names:
                raise WpsImageError('存在WPS单元格图片公式，但文件内缺少图片索引；不能完整转换')
            rels = relationships(z, part)
            pictures = {}
            for pic in ET.fromstring(z.read(part)).findall(f'.//{{{XDR}}}pic'):
                info = pic.find(f'{{{XDR}}}nvPicPr/{{{XDR}}}cNvPr')
                blip = pic.find(f'{{{XDR}}}blipFill/{{{A}}}blip')
                if info is None or blip is None:
                    continue
                image_id = info.get('name')
                if image_id in pictures:
                    raise WpsImageError('WPS图片ID重复，无法确定正确图片')
                pictures[image_id] = blip.get(f'{{{R}}}embed')
            result = []
            cache = {}
            for sheet, address, bounds, image_id in references:
                rel = rels.get(pictures.get(image_id))
                if rel is None or rel.get('TargetMode') == 'External' or not (rel.get('Type') or '').endswith('/image'):
                    raise WpsImageError(f'{sheet}!{address} 的WPS图片未嵌入文件或引用丢失；不能完整转换')
                image_part = part_path(part, rel.get('Target'))
                if not image_part.startswith('xl/media/') or image_part not in names:
                    raise WpsImageError(f'{sheet}!{address} 缺少嵌入图片文件')
                if image_part not in cache:
                    info = z.getinfo(image_part)
                    if info.file_size > 40 * 1024 * 1024:
                        raise WpsImageError('单张WPS图片超过40MB上限')
                    data = z.read(image_part)
                    if not (data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8\xff')):
                        raise WpsImageError('WPS单元格图片目前只支持嵌入PNG/JPEG，请转换为普通图片')
                    cache[image_part] = data
                result.append(CellImage(sheet, address, bounds, image_id, image_part, cache[image_part]))
            return result
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise WpsImageError('无法解析工作簿的WPS图片结构') from e


def fit_inside(x, y, width, height, image_width, image_height, padding=25):
    """Aspect-fit into a cell/merged range in LibreOffice's 1/100 mm units."""
    if min(width, height, image_width, image_height) <= 0:
        raise WpsImageError('WPS图片或单元格尺寸无效')
    inset = min(padding, width // 10, height // 10)
    available_width, available_height = width - 2 * inset, height - 2 * inset
    scale = min(available_width / image_width, available_height / image_height)
    w, h = max(1, round(image_width * scale)), max(1, round(image_height * scale))
    return x + (width - w) // 2, y + (height - h) // 2, w, h
