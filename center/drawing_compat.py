"""Suppress zero-area filled OOXML remnants in the render model only.

Stdlib only: also imported by the isolated distro-Python UNO process.
Never rewrite the source package or infer visibility from a shape's colour.
"""
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET
import zipfile

try:
    from .wps_images import A, S, R, XDR, relationships, part_path
except ImportError:  # Direct UNO script execution
    from wps_images import A, S, R, XDR, relationships, part_path


@dataclass(frozen=True)
class IgnoredDrawing:
    sheet: str
    name: str
    reason: str


def zero_axis(shape):
    ext = shape.find(f'{{{XDR}}}spPr/{{{A}}}xfrm/{{{A}}}ext')
    if ext is None:
        return False
    try:
        width, height = int(ext.get('cx', '-1')), int(ext.get('cy', '-1'))
    except ValueError:
        return False
    return min(width, height) == 0


def load_ignored_drawings(source):
    if Path(source).suffix.lower() != '.xlsx':
        return []
    result = []
    with zipfile.ZipFile(source) as package:
        if sum(i.file_size for i in package.infolist()) > 200 * 1024 * 1024:
            raise ValueError('Excel解压体积超过200MB')
        workbook = ET.fromstring(package.read('xl/workbook.xml'))
        rels = relationships(package, 'xl/workbook.xml')
        for sheet in workbook.findall(f'{{{S}}}sheets/{{{S}}}sheet'):
            rel = rels.get(sheet.get(f'{{{R}}}id'))
            if rel is None or rel.get('TargetMode') == 'External':
                raise ValueError('工作表引用缺失或指向外部文件')
            if not rel.get('Type', '').endswith('/worksheet'):
                continue
            part = part_path('xl/workbook.xml', rel.get('Target'))
            tree = ET.fromstring(package.read(part))
            drawing_rels = relationships(package, part)
            objects, names = [], Counter()
            for drawing in tree.findall(f'{{{S}}}drawing'):
                link = drawing_rels.get(drawing.get(f'{{{R}}}id'))
                if link is None or link.get('TargetMode') == 'External':
                    raise ValueError('图形引用缺失或指向外部文件')
                root = ET.fromstring(package.read(part_path(part, link.get('Target'))))
                names.update(n.get('name') for n in root.iter(f'{{{XDR}}}cNvPr'))
                for anchor in root.findall(f'{{{XDR}}}twoCellAnchor'):
                    for tag in ('sp', 'cxnSp'):
                        for shape in anchor.findall(f'{{{XDR}}}{tag}'):
                            info = shape.find(f'.//{{{XDR}}}cNvPr')
                            if info is not None:
                                objects.append((tag, shape, info))
            protected = set()
            for tag, shape, _ in objects:
                if tag == 'cxnSp':
                    endpoints = [e for name in ('stCxn', 'endCxn')
                                 for e in shape.findall(f'.//{{{A}}}{name}')]
                    if len(endpoints) == 2:
                        protected.update(e.get('id') for e in endpoints)
            ignored = []
            for tag, shape, info in objects:
                props = shape.find(f'{{{XDR}}}spPr')
                geometry = None if props is None else props.find(f'{{{A}}}prstGeom')
                filled = props is not None and any(props.find(f'{{{A}}}{f}') is not None
                                                  for f in ('solidFill', 'gradFill', 'pattFill', 'blipFill'))
                if (tag == 'sp' and zero_axis(shape) and filled and geometry is not None
                        and geometry.get('prst') != 'line' and info.get('id') not in protected
                        and not any((t.text or '').strip() for t in shape.iter(f'{{{A}}}t'))):
                    ignored.append((info, 'zero_area_fill'))
            ids = {info.get('id') for info, _ in ignored}
            for tag, shape, info in objects:
                endpoints = [e for name in ('stCxn', 'endCxn')
                             for e in shape.findall(f'.//{{{A}}}{name}')]
                if tag == 'cxnSp' and zero_axis(shape) and len(endpoints) == 1 and endpoints[0].get('id') in ids:
                    ignored.insert(0, (info, 'orphan_connector'))
            for info, reason in ignored:
                name = info.get('name')
                if not name or names[name] != 1:
                    raise ValueError('零尺寸残留图形名称不唯一，无法安全转换')
                result.append(IgnoredDrawing(sheet.get('name'), name, reason))
    return result


def remove_ignored_drawings(document, ignored):
    pending = []
    for item in ignored:
        page = document.Sheets.getByName(item.sheet).DrawPage
        matches = [page.getByIndex(i) for i in range(page.Count)
                   if page.getByIndex(i).Name == item.name]
        if len(matches) != 1:
            raise ValueError('零尺寸残留图形无法唯一映射到渲染模型，转换已停止')
        pending.append((page, matches[0]))
    for page, shape in pending:
        page.remove(shape)
    return len(pending)
