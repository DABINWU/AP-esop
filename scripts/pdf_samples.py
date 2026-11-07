"""Small vector PDF fixtures for parser/raster integration; no customer document data."""


def sample_pdf(sizes=((960, 540),)):
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    kids = []
    for i, (width, height) in enumerate(sizes):
        page_id = len(objects) + 1
        kids.append(f'{page_id} 0 R')
        objects.append((f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] '
                        f'/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id+1} 0 R >>').encode())
        content = (f'0.08 0.4 0.35 rg 20 20 {width-40} {height-40} re f\n'
                   f'1 1 1 rg BT /F1 24 Tf 40 {height-65} Td (PDF SAMPLE - PAGE {i+1}) Tj ET\n'
                   f'1 0.85 0 rg 50 {height//2} 60 50 re f\n'
                   f'1 0.85 0 rg 160 {height//2} 60 50 re f\n').encode()
        objects.append(f'<< /Length {len(content)} >>\nstream\n'.encode()+content+b'endstream')
    objects[1] = f'<< /Type /Pages /Count {len(kids)} /Kids [{" ".join(kids)}] >>'.encode()
    result = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n')
    xref = len(result)
    result.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]: result.extend(f'{offset:010d} 00000 n \n'.encode())
    result.extend((f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n'
                   f'startxref\n{xref}\n%%EOF\n').encode())
    return bytes(result)
