from typing import Literal

Category = Literal['production', 'inspection', 'check_video', 'custom']
MODULES = {'production':'生产作业指导书', 'inspection':'检验作业指导书',
           'check_video':'点检视频', 'custom':'自定义'}


def manifest_modules(items):
    return [dict(id=key, label=label, loop=True,
                 items=[item for item in items if item.get('category', 'custom') == key])
            for key, label in MODULES.items()]
