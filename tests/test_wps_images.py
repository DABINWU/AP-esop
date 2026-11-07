import hashlib
import zipfile
from xml.etree import ElementTree as ET

import pytest

from center.wps_images import (A, P, R, S, XDR, WpsImageError, containing_range,
                               fit_inside, load_cell_images, part_path)
from test_center import env, png_bytes
from center.worker import process_one


def sample(path, formula='_xlfn.DISPIMG("ID_A",1)', *, missing=None, target='media/picture.png', external=False,
           image_data=None, duplicate=False):
    workbook = ET.Element(f'{{{S}}}workbook')
    sheets = ET.SubElement(workbook, f'{{{S}}}sheets')
    ET.SubElement(sheets, f'{{{S}}}sheet', {'name':'WPS测试', 'sheetId':'1', f'{{{R}}}id':'sheet1'})
    sheet = ET.Element(f'{{{S}}}worksheet')
    row = ET.SubElement(ET.SubElement(sheet,f'{{{S}}}sheetData'),f'{{{S}}}row',{'r':'2'})
    cell = ET.SubElement(row,f'{{{S}}}c',{'r':'B2'})
    ET.SubElement(cell,f'{{{S}}}f').text=formula
    merges=ET.SubElement(sheet,f'{{{S}}}mergeCells')
    ET.SubElement(merges,f'{{{S}}}mergeCell',{'ref':'B2:C3'})
    wb_rels=ET.Element(f'{{{P}}}Relationships')
    ET.SubElement(wb_rels,f'{{{P}}}Relationship',{'Id':'sheet1','Type':R+'/worksheet','Target':'worksheets/sheet1.xml'})
    pictures=ET.Element('cellImages')
    for _ in range(2 if duplicate else 1):
        pic=ET.SubElement(ET.SubElement(pictures,'cellImage'),f'{{{XDR}}}pic')
        info=ET.SubElement(pic,f'{{{XDR}}}nvPicPr')
        ET.SubElement(info,f'{{{XDR}}}cNvPr',{'id':'1','name':'ID_A'})
        fill=ET.SubElement(pic,f'{{{XDR}}}blipFill')
        ET.SubElement(fill,f'{{{A}}}blip',{f'{{{R}}}embed':'image1'})
    image_rels=ET.Element(f'{{{P}}}Relationships')
    attributes={'Id':'image1','Type':R+'/image','Target':target}
    if external:attributes['TargetMode']='External'
    ET.SubElement(image_rels,f'{{{P}}}Relationship',attributes)
    parts={'xl/workbook.xml':ET.tostring(workbook),'xl/_rels/workbook.xml.rels':ET.tostring(wb_rels),
           'xl/worksheets/sheet1.xml':ET.tostring(sheet),'xl/cellimages.xml':ET.tostring(pictures),
           'xl/_rels/cellimages.xml.rels':ET.tostring(image_rels),
           'xl/media/picture.png':png_bytes() if image_data is None else image_data}
    with zipfile.ZipFile(path,'w') as z:
        for name,data in parts.items():
            if name != missing:z.writestr(name,data)
    return path


def test_resolve_wps_cell_image_and_merged_bounds_preserves_original(tmp_path):
    path=sample(tmp_path/'wps.xlsx')
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    images=load_cell_images(path)
    assert len(images)==1
    assert (images[0].sheet_name, images[0].cell, images[0].bounds)==('WPS测试','B2','B2:C3')
    assert images[0].data==png_bytes() and images[0].package_path=='xl/media/picture.png'
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before


@pytest.mark.parametrize('formula',['DISPIMG("ID_A", 1)','@_xlfn.DISPIMG("ID_A",1)', '=DISPIMG("ID_A",1)'])
def test_supported_literal_variants(tmp_path,formula):
    assert len(load_cell_images(sample(tmp_path/'wps.xlsx',formula)))==1


@pytest.mark.parametrize('formula',['SUM(1,2)','"DISPIMG("','IF(1,"DISPIMG(","text")'])
def test_ordinary_formulas_and_literal_strings_are_untouched(tmp_path,formula):
    assert load_cell_images(sample(tmp_path/'normal.xlsx',formula))==[]


@pytest.mark.parametrize('formula',['DISPIMG("ID_A",2)','DISPIMG(A1,1)','IF(A1,DISPIMG("ID_A",1),"")'])
def test_unsupported_modes_dynamic_and_compound_formulas_fail_closed(tmp_path,formula):
    with pytest.raises(WpsImageError,match='不受支持'):
        load_cell_images(sample(tmp_path/'wps.xlsx',formula))


@pytest.mark.parametrize('missing',['xl/cellimages.xml','xl/_rels/cellimages.xml.rels','xl/media/picture.png'])
def test_missing_picture_or_relationship_fails_closed(tmp_path,missing):
    with pytest.raises(WpsImageError):load_cell_images(sample(tmp_path/'wps.xlsx',missing=missing))


@pytest.mark.parametrize('target,external',[('https://example.invalid/photo.png',True),
                                          ('../../secret.png',False),('file:///etc/passwd',False)])
def test_external_and_traversal_paths_are_never_fetched(tmp_path,target,external):
    with pytest.raises(WpsImageError):load_cell_images(sample(tmp_path/'wps.xlsx',target=target,external=external))


def test_duplicate_ids_and_unsupported_media_fail(tmp_path):
    with pytest.raises(WpsImageError,match='重复'):load_cell_images(sample(tmp_path/'duplicate.xlsx',duplicate=True))
    with pytest.raises(WpsImageError,match='PNG/JPEG'):load_cell_images(sample(tmp_path/'bad.xlsx',image_data=b'not a raster image'))


def test_non_excel_and_plain_workbooks_have_no_adapter(tmp_path):
    assert load_cell_images(tmp_path/'old.xls')==[]


def test_aspect_fit_stays_inside_original_range_without_distortion():
    x,y,w,h=fit_inside(100,200,4000,2000,800,800)
    assert x>=100 and y>=200 and x+w<=4100 and y+h<=2200
    assert w==h
    assert containing_range('C3',['B2:D4'])=='B2:D4'
    assert containing_range('A1',['B2:D4'])=='A1'
    assert part_path('xl/cellimages.xml','/xl/media/a.png')=='xl/media/a.png'
    with pytest.raises(WpsImageError):fit_inside(0,0,0,10,20,20)


def test_corrupt_embedded_png_fails_job_before_libreoffice(env, tmp_path, monkeypatch):
    client,db,admin,_=env
    path=sample(tmp_path/'corrupt.xlsx',image_data=b'\x89PNG\r\n\x1a\nnot actually an image')
    response=client.post('/api/v1/assets',headers=admin,files={'file':('corrupt.xlsx',path.read_bytes())})
    assert response.status_code==200,response.text
    asset=response.json()
    def unexpected_command(*args):
        pytest.fail('Invalid image must be rejected before launching LibreOffice')
    monkeypatch.setattr('center.worker.run_command',unexpected_command)
    assert process_one(db)
    failed=next(a for a in client.get('/api/v1/assets',headers=admin).json()['assets'] if a['id']==asset['id'])
    assert failed['status']=='failed' and failed['conversion_job']['status']=='failed'
