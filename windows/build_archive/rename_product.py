"""Name-only update of the clean final release; preserve data compatibility."""
import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'dist/图鉴'
NEW=ROOT/'dist/图片查重系统'
INNER=NEW/'程序文件'
WORK=ROOT/'rename_work'
VALID=ROOT/'rename_validation'
BRAND='图片查重系统'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,o):p.write_text(json.dumps(o,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

if NEW.exists():raise RuntimeError('New folder already exists; refusing overwrite')
if (OLD/'程序文件/user_data').exists():raise RuntimeError('Source has user data; use the clean ZIP instead')
WORK.mkdir(exist_ok=True);VALID.mkdir(exist_ok=True)
shutil.copytree(OLD,NEW)
(NEW/'启动图鉴.exe').rename(NEW/'启动图片查重系统.exe')

changes=[]
def replace_text(path, *, preserve_inner_exe=False):
    before=path.read_bytes()
    content=before.decode('utf-8-sig')
    if preserve_inner_exe:
        content=content.replace('"图鉴.exe"','"__KEEP_INTERNAL_EXE__"')
    content=content.replace('图鉴',BRAND)
    if preserve_inner_exe:content=content.replace('"__KEEP_INTERNAL_EXE__"','"图鉴.exe"')
    encoding='utf-8-sig' if before.startswith(b'\xef\xbb\xbf') else 'utf-8'
    path.write_bytes(content.encode(encoding))
    changes.append(path.relative_to(NEW).as_posix())

replace_text(NEW/'使用说明.txt')
replace_text(INNER/'release_source/app/static/index.html')
replace_text(INNER/'release_source/app/static/app.js')
html=INNER/'release_source/app/static/index.html'
text=html.read_text(encoding='utf-8')
# Six-character name fits the same existing sidebar; no sidebar width or layout changes.
needle='<span>图片查重系统<small>'
assert text.count(needle)==1
text=text.replace(needle,'<span style="font-size:18px;letter-spacing:0;white-space:nowrap">图片查重系统<small>')
html.write_text(text,encoding='utf-8')

for filename in ('DeliveryEntry.cs','TujianLauncher.cs'):
    source=INNER/'维护资料'/filename
    replace_text(source,preserve_inner_exe=True)
    shutil.copy2(source,WORK/filename)
replace_text(INNER/'tools/layout_migration.py',preserve_inner_exe=True)
shutil.copy2(INNER/'tools/layout_migration.py',INNER/'维护资料/layout_migration.py')
for name in ('维护复现说明.md',):
    replace_text(INNER/'维护资料'/name,preserve_inner_exe=True)

compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
for source,target in [('DeliveryEntry.cs',NEW/'启动图片查重系统.exe'),('DeliveryEntry.cs',NEW/'迁移数据.exe'),('TujianLauncher.cs',INNER/'图鉴.exe')]:
    subprocess.run([str(compiler),'/nologo','/target:winexe','/platform:x64','/optimize+','/codepage:65001','/out:'+str(target),
        '/win32icon:'+str(INNER/'tujian-liquid-glass-v1.ico'),'/resource:'+str(INNER/'维护资料/tujian-liquid-glass-v1.png')+',tujian.png',
        '/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll','/reference:System.Web.Extensions.dll',str(WORK/source)],
        check=True,creationflags=subprocess.CREATE_NO_WINDOW)
    changes.append(target.relative_to(NEW).as_posix())

# Keep all app Python and legacy control tools byte-for-byte intact. Their
# hashes define the existing backup/restore compatibility contract.
protected=[]
for folder,glob in [('release_source/app','*.py'),('tools','*.py')]:
    for original in (OLD/'程序文件'/folder).glob(glob):
        if original.name=='layout_migration.py':continue
        rel=original.relative_to(OLD/'程序文件')
        assert original.read_bytes()==(INNER/rel).read_bytes(),str(rel)
        protected.append(str(rel))
for relative in ('release_source/app/static/app.css','tujian-liquid-glass-v1.ico','维护资料/tujian-liquid-glass-v1.png','release_source/requirements-lock.txt'):
    assert (OLD/'程序文件'/relative).read_bytes()==(INNER/relative).read_bytes()

sm=json.loads((INNER/'source_manifest.json').read_text(encoding='utf-8'))
for name in sm['files']:sm['files'][name]=sha(INNER/name)
sm['display_name']=BRAND
dump(INNER/'source_manifest.json',sm)
dump(INNER/'outer_manifest.json',{'schema':'tujian-outer-layout-v1','files':{p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in NEW.iterdir() if p.is_file()}})
dump(INNER/'file_manifest.json',{'schema':'tujian-inner-layout-v1','files':{p.relative_to(INNER).as_posix():{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(INNER.rglob('*')) if p.is_file() and p.name!='file_manifest.json'}})
assert set(p.name for p in NEW.iterdir())=={'程序文件','启动图片查重系统.exe','迁移数据.exe','使用说明.txt'}
report={'display_name':BRAND,'folder':str(NEW),'changed_display_assets':changes,'unchanged_python_files':protected,
        'icon_unchanged':True,'css_unchanged':True,'user_data_touched':False,
        'legacy_internal_names_retained':True,'legacy_python_report_templates_unchanged':True}
dump(VALID/'rename_changes.json',report)
print(json.dumps({'folder':str(NEW),'protected_python_files':len(protected),'new_name':BRAND},ensure_ascii=False))
