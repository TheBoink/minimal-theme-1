"""Run on a computer: python convert_logo.py input.png startup_logo.rgb565

Requires Pillow: python -m pip install Pillow
The image is resized to 240 x 240 and saved as big-endian RGB565 pixels for the
Waveshare LCD driver, which sends the framebuffer bytes directly over SPI.
"""
import sys
from PIL import Image


def convert(source, destination):
    image = Image.open(source).convert('RGB').resize((240, 240), Image.Resampling.LANCZOS)
    with open(destination, 'wb') as output:
        for red, green, blue in image.getdata():
            pixel = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
            output.write(bytes((pixel >> 8, pixel & 255)))


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('Usage: python convert_logo.py input.png startup_logo.rgb565')
    convert(sys.argv[1], sys.argv[2])
