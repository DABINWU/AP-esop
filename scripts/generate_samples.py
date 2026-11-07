"""Generate basic layout samples, not a substitute for user's real Excel acceptance."""
import argparse
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.worksheet.pagebreak import Break
from PIL import Image, ImageDraw
import xlwt


def wps_samples(root):
    """Synthetic OOXML interoperability fixture; contains no customer media."""
    s='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    p='http://schemas.openxmlformats.org/package/2006/relationships'
    a='http://schemas.openxmlformats.org/drawingml/2006/main'
    xdr='http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing'
    ct='http://schemas.openxmlformats.org/package/2006/content-types'
    with zipfile.ZipFile(root/'basic-layout.xlsx') as z:
        parts={name:z.read(name) for name in z.namelist()}
    sheet=ET.fromstring(parts['xl/worksheets/sheet1.xml'])
    cell=next(c for c in sheet.iter(f'{{{s}}}c') if c.get('r')=='F5')
    for value in list(cell):cell.remove(value)
    cell.set('t','str')
    ET.SubElement(cell,f'{{{s}}}f').text='_xlfn.DISPIMG("ID_ESOP_SAMPLE",1)'
    ET.SubElement(cell,f'{{{s}}}v').text='=DISPIMG("ID_ESOP_SAMPLE",1)'
    parts['xl/worksheets/sheet1.xml']=ET.tostring(sheet)
    images=ET.Element('{http://www.wps.cn/officeDocument/2017/etCustomData}cellImages')
    pic=ET.SubElement(ET.SubElement(images,'{http://www.wps.cn/officeDocument/2017/etCustomData}cellImage'),f'{{{xdr}}}pic')
    ET.SubElement(ET.SubElement(pic,f'{{{xdr}}}nvPicPr'),f'{{{xdr}}}cNvPr',{'id':'101','name':'ID_ESOP_SAMPLE'})
    ET.SubElement(ET.SubElement(pic,f'{{{xdr}}}blipFill'),f'{{{a}}}blip',{f'{{{r}}}embed':'rId1'})
    parts['xl/cellimages.xml']=ET.tostring(images)
    rels=ET.Element(f'{{{p}}}Relationships')
    ET.SubElement(rels,f'{{{p}}}Relationship',{'Id':'rId1','Type':r+'/image','Target':'media/image1.png'})
    parts['xl/_rels/cellimages.xml.rels']=ET.tostring(rels)
    rels=ET.fromstring(parts['xl/_rels/workbook.xml.rels'])
    ET.SubElement(rels,f'{{{p}}}Relationship',{'Id':'rIdWpsFixture','Type':'http://www.wps.cn/officeDocument/2020/cellImage','Target':'cellimages.xml'})
    parts['xl/_rels/workbook.xml.rels']=ET.tostring(rels)
    types=ET.fromstring(parts['[Content_Types].xml'])
    ET.SubElement(types,f'{{{ct}}}Override',{'PartName':'/xl/cellimages.xml','ContentType':'application/vnd.wps-officedocument.cellimage+xml'})
    parts['[Content_Types].xml']=ET.tostring(types)
    for filename,missing in [('wps-cell-images.xlsx',None),('wps-missing-image.xlsx','xl/_rels/cellimages.xml.rels')]:
        with zipfile.ZipFile(root/filename,'w',zipfile.ZIP_DEFLATED) as z:
            for name,data in parts.items():
                if name!=missing:z.writestr(name,data)


def generate(root):
    root.mkdir(parents=True, exist_ok=True)
    logo = root / "embedded.png"
    image = Image.new("RGB",(240,80),"#126655")
    ImageDraw.Draw(image).text((20,25),"ESOP / SAMPLE",fill="white")
    image.save(logo)
    workbook = Workbook()
    sheet = workbook.active; sheet.title = "装配工艺"
    sheet.merge_cells("A1:D2"); sheet["A1"] = "装配工艺指导书 / 基础分页样例"
    sheet["A1"].font = Font(name="Noto Sans CJK SC",size=20,bold=True)
    sheet["A1"].alignment = Alignment(horizontal="center",vertical="center")
    sheet.append([])
    headers = ["序号","工序","操作说明","参数","检验","备注"]
    for col, name in enumerate(headers,1): sheet.cell(4,col,name)
    for row in range(5,78):
        for col, value in enumerate([row-4,"安装组件",f"第 {row-4} 步：对齐定位后锁紧；核对标记。","5 ± 0.2 Nm","目视 + 扭矩","样例 / 待真实文件核对"],1):
            sheet.cell(row,col,value)
    thin = Side(style="thin",color="607D75")
    for row in sheet:
        for cell in row:
            if cell.row > 2: cell.font = Font(name="Noto Sans CJK SC",size=11)
            cell.border = Border(left=thin,right=thin,top=thin,bottom=thin)
            if cell.row > 2: cell.alignment = Alignment(vertical="center",wrap_text=True)
            if cell.row==4: cell.fill=PatternFill("solid",fgColor="D7EEE7")
    for col,width in zip("ABCDEF",[8,16,48,18,18,28]): sheet.column_dimensions[col].width=width
    for row in range(1,78): sheet.row_dimensions[row].height=30
    embedded=ExcelImage(str(logo)); embedded.width=210; embedded.height=70
    sheet.add_image(embedded,"E1")
    sheet.print_area = "A1:F77"; sheet.print_title_rows="1:4"
    sheet.page_setup.orientation="landscape"; sheet.page_setup.paperSize=sheet.PAPERSIZE_A4
    sheet.sheet_properties.pageSetUpPr.fitToPage=True
    sheet.page_setup.fitToWidth=1; sheet.page_setup.fitToHeight=0
    sheet.row_breaks.append(Break(id=40))
    workbook.save(root/"basic-layout.xlsx")
    sheet.print_area=None; workbook.save(root/"no-print-area.xlsx")
    legacy = xlwt.Workbook(); s=legacy.add_sheet("旧版工艺")
    style=xlwt.easyxf('font: name Noto Sans CJK SC, height 240; borders: left thin,right thin,top thin,bottom thin;')
    for row in range(60):
        for col,value in enumerate([row+1,"XLS 工艺样例","请核对中文字体和分页", "检验通过"]): s.write(row,col,value,style)
        s.row(row).height=400
    for col in range(4): s.col(col).width=7000
    s.portrait=False; s.fit_num_pages=1; s.fit_width_to_pages=1; s.fit_height_to_pages=0
    legacy.save(str(root/"basic-legacy.xls"))
    (root/"broken.xls").write_bytes(bytes.fromhex("d0cf11e0a1b11ae1")+b"broken workbook for failure validation")
    wps_samples(root)
    return root


if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--output",default="test-output/samples")
    print(generate(Path(parser.parse_args().output).resolve()))
