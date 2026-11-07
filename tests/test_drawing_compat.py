import zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from center.drawing_compat import load_ignored_drawings, remove_ignored_drawings
from center.wps_images import A, XDR, S, R, P
from test_wps_images import sample


def drawing_sample(path, *, text=False, fill=True, width=0, both=False, duplicate=False):
    sample(path)
    with zipfile.ZipFile(path) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    sheet = ET.fromstring(parts['xl/worksheets/sheet1.xml'])
    ET.SubElement(sheet, f'{{{S}}}drawing', {f'{{{R}}}id': 'draw1'})
    parts['xl/worksheets/sheet1.xml'] = ET.tostring(sheet)
    rels = ET.Element(f'{{{P}}}Relationships')
    ET.SubElement(rels, f'{{{P}}}Relationship', {'Id': 'draw1', 'Type': R+'/drawing', 'Target': '../drawings/drawing1.xml'})
    parts['xl/worksheets/_rels/sheet1.xml.rels'] = ET.tostring(rels)
    root = ET.Element(f'{{{XDR}}}wsDr')
    for id_, geometry, cx in [('1', 'rect', width), ('2', 'downArrow', 100), ('3', 'line', 0)]:
        shape = ET.SubElement(ET.SubElement(root, f'{{{XDR}}}twoCellAnchor'), f'{{{XDR}}}sp')
        nv = ET.SubElement(shape, f'{{{XDR}}}nvSpPr')
        ET.SubElement(nv, f'{{{XDR}}}cNvPr', {'id': id_, 'name': 'shape'+('1' if duplicate else id_)})
        props = ET.SubElement(shape, f'{{{XDR}}}spPr')
        ET.SubElement(ET.SubElement(props, f'{{{A}}}xfrm'), f'{{{A}}}ext', {'cx': str(cx), 'cy': '100'})
        ET.SubElement(props, f'{{{A}}}prstGeom', {'prst': geometry})
        if fill: ET.SubElement(props, f'{{{A}}}solidFill')
        if text: ET.SubElement(shape, f'{{{A}}}t').text = 'keep text'
    connector = ET.SubElement(ET.SubElement(root, f'{{{XDR}}}twoCellAnchor'), f'{{{XDR}}}cxnSp')
    nv = ET.SubElement(connector, f'{{{XDR}}}nvCxnSpPr')
    ET.SubElement(nv, f'{{{XDR}}}cNvPr', {'id': '4', 'name': 'connector'})
    ET.SubElement(nv, f'{{{A}}}stCxn', {'id': '1'})
    if both: ET.SubElement(nv, f'{{{A}}}endCxn', {'id': '2'})
    props = ET.SubElement(connector, f'{{{XDR}}}spPr')
    ET.SubElement(ET.SubElement(props, f'{{{A}}}xfrm'), f'{{{A}}}ext', {'cx': '0', 'cy': '100'})
    parts['xl/drawings/drawing1.xml'] = ET.tostring(root)
    with zipfile.ZipFile(path, 'w') as z:
        for n, data in parts.items(): z.writestr(n, data)
    return path


def test_only_zero_area_filled_remnants_and_orphan_removed(tmp_path):
    path = drawing_sample(tmp_path/'shapes.xlsx')
    before = path.read_bytes()
    ignored = load_ignored_drawings(path)
    assert [(i.name, i.reason) for i in ignored] == [('connector', 'orphan_connector'), ('shape1', 'zero_area_fill')]
    assert path.read_bytes() == before
    class Page:
        def __init__(self): self.shapes = [SimpleNamespace(Name=n) for n in ('shape1', 'shape2', 'shape3', 'connector')]
        @property
        def Count(self): return len(self.shapes)
        def getByIndex(self, i): return self.shapes[i]
        def remove(self, shape): self.shapes.remove(shape)
    page = Page()
    document = SimpleNamespace(Sheets=SimpleNamespace(getByName=lambda name: SimpleNamespace(DrawPage=page)))
    assert remove_ignored_drawings(document, ignored) == 2
    assert [s.Name for s in page.shapes] == ['shape2', 'shape3']
    with pytest.raises(ValueError, match='唯一映射'): remove_ignored_drawings(document, ignored)


@pytest.mark.parametrize('options', [{'text': True}, {'fill': False}, {'width': 100}, {'both': True}])
def test_preserve_visible_text_outline_shapes_and_two_ended_connectors(tmp_path, options):
    assert load_ignored_drawings(drawing_sample(tmp_path/'keep.xlsx', **options)) == []


def test_ambiguous_names_fail_closed(tmp_path):
    with pytest.raises(ValueError, match='名称不唯一'):
        load_ignored_drawings(drawing_sample(tmp_path/'duplicate.xlsx', duplicate=True))


def test_legacy_xls_is_not_rewritten(tmp_path):
    assert load_ignored_drawings(tmp_path/'legacy.xls') == []
