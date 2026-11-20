from pathlib import Path

from PIL import Image, ImageFilter


def points(official):
    mask=official.convert("L").point(lambda pixel: 255 if pixel<180 else 0)
    dark=mask.filter(ImageFilter.MinFilter(9))
    light=mask.filter(ImageFilter.MaxFilter(9))
    return [(column,row,int(dark.getpixel((column,row))==255))
            for row in range(90,525,19) for column in range(85,521,19)
            if dark.getpixel((column,row))==255 or light.getpixel((column,row))==0]


if __name__=="__main__":
    official=Image.open("aidoku-cover-v2.png")
    samples=points(official)
    if not 200<=len(samples)<=600 or not 100<=sum(item[2] for item in samples)<=300:
        raise ValueError("unexpected official logo geometry")
    Path("mark_points.h").write_text("static const int mark_points[][3]={\n"+
        "".join("{%d,%d,%d},\n"%item for item in samples)+"};\n",encoding="ascii")
