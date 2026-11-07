"""Fit a rendered whole worksheet to the terminal canvas without stretching/cropping."""
from PIL import Image, ImageOps

SCREEN_SIZE = (1920, 1080)


def fit_screen_page(path):
    with Image.open(path) as source:
        image = ImageOps.contain(source.convert('RGB'), SCREEN_SIZE, Image.Resampling.LANCZOS)
    with Image.new('RGB', SCREEN_SIZE, 'white') as canvas:
        canvas.paste(image, ((SCREEN_SIZE[0] - image.width) // 2,
                             (SCREEN_SIZE[1] - image.height) // 2))
        canvas.save(path, 'PNG')
    image.close()
