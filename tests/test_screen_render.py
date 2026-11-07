import pytest
from PIL import Image, ImageChops

from center.screen_render import fit_screen_page


@pytest.mark.parametrize('size', [(1600, 400), (400, 1600), (1920, 1080), (600, 600), (16, 9)])
def test_screen_canvas_preserves_full_image_and_aspect(tmp_path, size):
    path = tmp_path / 'page.png'
    Image.new('RGB', size, '#148675').save(path)
    fit_screen_page(path)
    with Image.open(path) as image:
        assert image.size == (1920, 1080)
        box = ImageChops.difference(image, Image.new('RGB', image.size, 'white')).getbbox()
        left, top, right, bottom = box
        scale = min(1920 / size[0], 1080 / size[1])
        assert abs((right - left) - size[0] * scale) <= 1
        assert abs((bottom - top) - size[1] * scale) <= 1
        assert abs(left - (1920 - right)) <= 1
        assert abs(top - (1080 - bottom)) <= 1
        assert image.getpixel((left, top)) == (20, 134, 117)
        assert image.getpixel((right - 1, bottom - 1)) == (20, 134, 117)
