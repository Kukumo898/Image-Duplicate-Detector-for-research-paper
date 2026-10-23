"""Make a clean four-item distribution. Never reads the user's used package."""
import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'work'
OUTER = ROOT / 'dist' / '图鉴'
INNER = OUTER / '程序文件'
PREVIOUS = ROOT.parent / 'liquid_glass_icon'

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p, obj): p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
def compile_exe(source, target):
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/codepage:65001',
        '/out:' + str(target), '/win32icon:' + str(PREVIOUS / 'assets/tujian-liquid-glass-v1.ico'),
        '/resource:' + str(PREVIOUS / 'assets/tujian-liquid-glass-v1.png') + ',tujian.png',
        '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll', '/reference:System.Web.Extensions.dll', str(source)],
        check=True, creationflags=subprocess.CREATE_NO_WINDOW)

def build():
    if OUTER.exists(): raise RuntimeError('Refusing to overwrite existing distribution')
    INNER.mkdir(parents=True)
    with zipfile.ZipFile(PREVIOUS / 'dist/图鉴_液态玻璃版_1.0.2_Windows_x64.zip') as archive:
        for item in archive.infolist():
            rel = Path(*item.filename.split('/')[1:])
            if not rel.parts or item.is_dir(): continue
            if rel.is_absolute() or '..' in rel.parts: raise ValueError('unsafe zip member')
            path = INNER / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(item))
    # Preserve the full inner layout; only route existing maintenance menus to
    # the new outer migration utility, so restores cannot create a flat package.
    cs = (PREVIOUS / 'work/TujianLauncher.cs').read_text(encoding='utf-8')
    cs = cs.replace('delegate { StartOperation("backup"); }', 'delegate { Shell(Path.Combine(Directory.GetParent(root).FullName, "迁移数据.exe")); }')
    cs = cs.replace('delegate { StartOperation("restore"); }', 'delegate { Shell(Path.Combine(Directory.GetParent(root).FullName, "迁移数据.exe")); }')
    cs = cs.replace('Shell(Path.Combine(root, "图标入口说明.txt"))', 'Shell(Path.Combine(Directory.GetParent(root).FullName, "使用说明.txt"))')
    inner_source = WORK / 'TujianLauncher.cs'
    inner_source.write_text(cs, encoding='utf-8-sig')
    compile_exe(inner_source, INNER / '图鉴.exe')
    compile_exe(WORK / 'DeliveryEntry.cs', OUTER / '启动图鉴.exe')
    compile_exe(WORK / 'DeliveryEntry.cs', OUTER / '迁移数据.exe')
    shutil.copy2(WORK / '使用说明.txt', OUTER / '使用说明.txt')
    shutil.copy2(WORK / 'layout_migration.py', INNER / 'tools/layout_migration.py')
    maintenance = INNER / '维护资料'
    maintenance.mkdir()
    for name in ['DeliveryEntry.cs', 'TujianLauncher.cs', 'layout_migration.py', 'build_layout.py', '维护复现说明.md']:
        shutil.copy2(WORK / name, maintenance / name)
    shutil.copy2(ROOT.parent / 'desktop_entry/work/assets/generate_icon.py', maintenance / 'generate_original_icon.py')
    shutil.copy2(ROOT.parent.parent / 'scripts/create_demo.py', maintenance / 'create_demo.py')
    shutil.copy2(PREVIOUS / 'assets/tujian-glass-master.png', maintenance / 'icon-master.png')
    shutil.copy2(PREVIOUS / 'assets/tujian-liquid-glass-v1.png', maintenance / 'tujian-liquid-glass-v1.png')
    shutil.copy2(ROOT / 'review/GitHub公开发布检查.md', maintenance / 'GitHub发布说明.md')
    for origin, name in [('建议.gitignore', 'gitignore.sample'), ('GitHub上传路线README草稿.md', 'GitHub上传路线.md'), ('third_party_license_provenance.json', 'third_party_license_provenance.json')]:
        shutil.copy2(ROOT / 'review' / origin, maintenance / name)
    shutil.copy2(ROOT / 'review/AGPL-3.0-PyMuPDF-COPYING.txt', INNER / 'LICENSES/AGPL-3.0-PyMuPDF-COPYING.txt')
    # Register changed/new runnable inner files without involving user data.
    manifest = json.loads((INNER / 'source_manifest.json').read_text(encoding='utf-8'))
    manifest['final_layout_version'] = '1.1.0'
    for name in ('图鉴.exe', 'tools/layout_migration.py'):
        manifest['files'][name] = sha(INNER / name)
    dump(INNER / 'source_manifest.json', manifest)
    outer_files = {p.name: {'sha256': sha(p), 'bytes': p.stat().st_size} for p in OUTER.iterdir() if p.is_file()}
    dump(INNER / 'outer_manifest.json', {'schema': 'tujian-outer-layout-v1', 'files': outer_files})
    inner_files = {p.relative_to(INNER).as_posix(): {'sha256': sha(p), 'bytes': p.stat().st_size} for p in sorted(INNER.rglob('*')) if p.is_file() and p.name != 'file_manifest.json'}
    dump(INNER / 'file_manifest.json', {'schema': 'tujian-inner-layout-v1', 'files': inner_files})
    assert sorted(p.name for p in OUTER.iterdir()) == sorted(['程序文件', '启动图鉴.exe', '迁移数据.exe', '使用说明.txt'])
    assert not (INNER / 'user_data').exists()
    print(json.dumps({'folder': str(OUTER), 'inner_files': len(inner_files), 'outer_items': 4}, ensure_ascii=False))

if __name__ == '__main__': build()
