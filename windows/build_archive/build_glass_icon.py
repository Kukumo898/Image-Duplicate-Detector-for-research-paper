"""Format the generated art as Windows resources; no creative pixel editing."""
import hashlib
import json
import os
import struct
import subprocess
from pathlib import Path
from PIL import Image

root = Path(__file__).resolve().parents[1]
assets = root / 'assets'
source = assets / 'tujian-glass-master.png'
im = Image.open(source).convert('RGBA')
assert im.width == im.height
assert im.getchannel('A').getextrema()[0] == 0
png = assets / 'tujian-liquid-glass-v1.png'
ico = assets / 'tujian-liquid-glass-v1.ico'
im.resize((256, 256), Image.Resampling.LANCZOS).save(png)
im.save(ico, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
icon = Image.open(ico)
sizes = sorted(icon.ico.sizes())
assert len(sizes) == 7
for size in sizes:
    icon.ico.getimage(size).save(assets / ('size-%d.png' % size[0]))
compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
exe = root / 'build/图鉴.exe'
args = [str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
        '/out:' + str(exe), '/win32icon:' + str(ico), '/resource:' + str(png) + ',tujian.png',
        '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
        '/reference:System.Web.Extensions.dll', str(root / 'work/TujianLauncher.cs')]
subprocess.run(args, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
raw = exe.read_bytes()
pe = struct.unpack_from('<I', raw, 0x3c)[0]
assert struct.unpack_from('<H', raw, pe + 24 + 68)[0] == 2
original_source = root.parent / 'desktop_entry/work/TujianLauncher.cs'
assert original_source.read_bytes() == (root / 'work/TujianLauncher.cs').read_bytes()
report = {'generator': 'built-in imagegen', 'master_pixels': im.size, 'master_alpha_extrema': im.getchannel('A').getextrema(),
    'ico_sizes': sizes, 'source_code_byte_identical': True, 'pe_subsystem': 'Windows GUI',
    'exe_sha256': hashlib.sha256(raw).hexdigest(), 'ico_sha256': hashlib.sha256(ico.read_bytes()).hexdigest(),
    'raster_changes': 'format conversion and resolution downsampling only'}
(root / 'validation/build.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
