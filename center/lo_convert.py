"""Executed by distro Python (python3-uno), inside worker's timeout/process group.

Fresh profile and UNO flags both forbid macro execution and link updates.
No refreshAll()/calculateAll() or source workbook writes are performed.
"""
import argparse
import hashlib
import json
import subprocess
import time
import uuid
from pathlib import Path

import uno
from com.sun.star.beans import PropertyValue

from wps_images import load_cell_images, fit_inside
from drawing_compat import load_ignored_drawings, remove_ignored_drawings


def prop(name, value):
    p = PropertyValue()
    p.Name, p.Value = name, value
    return p


def restore_wps_images(context, document, images, scratch):
    if not images:
        return 0
    scratch.mkdir(parents=True, exist_ok=True)
    provider = context.ServiceManager.createInstanceWithContext('com.sun.star.graphic.GraphicProvider', context)
    cached = {}
    for image in images:
        if image.package_path not in cached:
            target = scratch / (hashlib.sha256(image.data).hexdigest() + '.img')
            target.write_bytes(image.data)
            graphic = provider.queryGraphic((prop('URL', target.as_uri()),))
            if graphic is None or graphic.SizePixel.Width <= 0 or graphic.SizePixel.Height <= 0:
                raise RuntimeError('WPS图片无法解码，不能完整转换')
            if graphic.SizePixel.Width * graphic.SizePixel.Height > 40_000_000:
                raise RuntimeError('WPS图片像素超过4000万上限')
            cached[image.package_path] = graphic
        graphic = cached[image.package_path]
        sheet = document.Sheets.getByName(image.sheet_name)
        cell = sheet.getCellRangeByName(image.cell)
        region = sheet.getCellRangeByName(image.bounds)
        # Clear value/formula only, retaining borders, fills, merges and cell formatting.
        cell.clearContents(1 | 2 | 4 | 16)
        if cell.getString() or cell.getFormula():
            raise RuntimeError(f'{image.cell} 的WPS图片占位公式无法清除，不能完整转换')
        x, y, width, height = fit_inside(region.Position.X, region.Position.Y,
            region.Size.Width, region.Size.Height, graphic.SizePixel.Width, graphic.SizePixel.Height)
        shape = document.createInstance('com.sun.star.drawing.GraphicObjectShape')
        sheet.DrawPage.add(shape)
        shape.Graphic = graphic
        shape.Anchor = cell
        shape.ResizeWithCell = True
        shape.Printable = True
        shape.Name = 'ESOP_WPS_' + image.cell
        point, size = uno.createUnoStruct('com.sun.star.awt.Point'), uno.createUnoStruct('com.sun.star.awt.Size')
        point.X, point.Y, size.Width, size.Height = x, y, width, height
        shape.setPosition(point)
        shape.setSize(size)
    return len(images)


def convert(source, output, profile, soffice, render_mode='print'):
    images = load_cell_images(source)
    ignored = load_ignored_drawings(source)
    user = profile / "user"
    user.mkdir(parents=True, exist_ok=True)
    (user / "registrymodifications.xcu").write_text('''<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting">
 <prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>
 <prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>
 <prop oor:name="SecureURL" oor:op="fuse"><value/></prop>
</item>
<item oor:path="/org.openoffice.Office.Calc/Content/Update">
 <prop oor:name="Link" oor:op="fuse"><value>1</value></prop>
</item>
</oor:items>''', encoding="utf-8")
    pipe = "esop_" + uuid.uuid4().hex
    process = subprocess.Popen([soffice, "-env:UserInstallation=" + profile.as_uri(), "--headless",
                                "--nologo", "--nodefault", "--norestore", "--nofirststartwizard",
                                f"--accept=pipe,name={pipe};urp;StarOffice.ComponentContext"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    document = desktop = None
    try:
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
        context = None
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("LibreOffice exited before connection")
            try:
                context = resolver.resolve(f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
                break
            except Exception:
                time.sleep(.1)
        if context is None:
            raise RuntimeError("LibreOffice UNO connection timeout")
        desktop = context.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", context)
        document = desktop.loadComponentFromURL(source.as_uri(), "_blank", 0, (
            # Compatibility edits affect the in-memory render copy only.
            # Export PDF only; never call store()/storeAsURL() on the Excel document.
            prop("Hidden", True), prop("ReadOnly", not bool(images or ignored)), prop("Silent", True),
            prop("UpdateDocMode", uno.getConstantByName("com.sun.star.document.UpdateDocMode.NO_UPDATE")),
            prop("MacroExecutionMode", uno.getConstantByName("com.sun.star.document.MacroExecMode.NEVER_EXECUTE")),
        ))
        if document is None or not document.supportsService("com.sun.star.sheet.SpreadsheetDocument"):
            raise RuntimeError("Cannot load an Excel spreadsheet")
        # Calc's autodetection can treat a broken .xls as plain CSV and still export a PDF.
        # Only real Excel import filters qualify as a successful conversion.
        loaded = {p.Name: p.Value for p in document.getArgs()}
        filter_name = str(loaded.get("FilterName", ""))
        supported = (filter_name.startswith("MS Excel ") if source.suffix.lower() == ".xls" else
                     filter_name in ("Calc MS Excel 2007 XML", "Calc Office Open XML"))
        if not supported:
            raise RuntimeError("文件损坏或不是受支持的 Excel 工作簿，拒绝按文本格式转换")
        removed = remove_ignored_drawings(document, ignored)
        restored = restore_wps_images(context, document, images, output.parent / 'wps-media')
        screen = render_mode == 'screen_16_9'
        filters = [prop('SinglePageSheets', screen), prop('UseLosslessCompression', True),
                   prop('ReduceImageResolution', False)]
        if screen:
            # Whole-sheet export ignores PageRange as well as print areas in LO25.
            # Return visible page indexes for filtering in the private worker workspace.
            visible = [i + 1 for i in range(document.Sheets.Count)
                       if document.Sheets.getByIndex(i).IsVisible]
            if not visible:
                raise RuntimeError('没有可见工作表，不能生成屏幕播放页面')
        # An untyped Python tuple becomes sequence<any>; Calc silently ignores it.
        filter_data = uno.Any('[]com.sun.star.beans.PropertyValue', tuple(filters))
        document.storeToURL(output.as_uri(), (prop("FilterName", "calc_pdf_Export"),
            prop("Overwrite", True), prop("FilterData", filter_data)))
        print(json.dumps({'wps_cell_images_restored': restored,
                          'ignored_degenerate_shapes': removed,
                          'visible_sheet_pages': visible if screen else None,
                          'sheet_count': document.Sheets.Count if screen else None}))
    finally:
        try:
            if document is not None:
                document.close(True)
            if desktop is not None:
                desktop.terminate()
        finally:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("source", "output", "profile", "soffice"):
        parser.add_argument(name)
    parser.add_argument('--render-mode', choices=('print', 'screen_16_9'), default='print')
    args = parser.parse_args()
    convert(Path(args.source).resolve(), Path(args.output).resolve(), Path(args.profile).resolve(), args.soffice, args.render_mode)
