"""Resize a copy; preserve original and refuse to overwrite an existing output."""
import argparse
from pathlib import Path
from PIL import Image, ImageOps
p=argparse.ArgumentParser(description=__doc__);p.add_argument('source');p.add_argument('output');p.add_argument('--max-size',type=int,default=1600)
a=p.parse_args()
if a.max_size<1: p.error('--max-size must be positive')
out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
with Image.open(a.source) as original:
    im=ImageOps.exif_transpose(original);im.thumbnail((a.max_size,a.max_size))
    fmt=Image.registered_extensions().get(out.suffix.lower())
    if not fmt: p.error('Unsupported output extension')
    if fmt=='JPEG': im=im.convert('RGB')
    with out.open('xb') as f: im.save(f,format=fmt)
print(out)
