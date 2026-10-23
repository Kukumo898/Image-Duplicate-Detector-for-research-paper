"""Small Windows ownership primitives; no application/database imports."""
from __future__ import annotations
import contextlib, ctypes, hashlib, json, msvcrt, os, socket, subprocess, time
from ctypes import wintypes as W
from pathlib import Path

RELEASE = Path(__file__).resolve().parents[1]
MAGIC = 'labimage-daily-runtime-v1'

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    with temp.open('w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
    os.replace(temp, path)

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()

def no_reparse(path):
    path = Path(path).absolute()
    for item in [path, *path.parents]:
        if item.exists() and (item.is_symlink() or item.is_junction()):
            raise RuntimeError('不接受符号链接或目录联接：' + str(item))
    return path.resolve()

@contextlib.contextmanager
def file_lock(path, timeout=0):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open('a+b'); f.seek(0)
    if path.stat().st_size == 0: f.write(b'0'); f.flush()
    end = time.monotonic() + timeout
    try:
        while True:
            try:
                f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1); break
            except OSError:
                if time.monotonic() >= end: raise RuntimeError('另一个启动／关闭／备份操作或服务仍占用本数据目录，请稍后重试。')
                time.sleep(.1)
        try: yield
        finally: f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    finally: f.close()

def service_locked(runtime):
    try:
        with file_lock(Path(runtime)/'service.lock'): pass
        return False
    except RuntimeError: return True

def verify_manifest():
    path = RELEASE/'source_manifest.json'; manifest = read_json(path)
    for rel, digest in manifest['files'].items():
        target = no_reparse(RELEASE/rel)
        if not target.is_relative_to(RELEASE) or sha(target) != digest:
            raise RuntimeError('交付文件校验失败，请使用完整交付副本：' + rel)
    return sha(path)

def runtime_marker(runtime, *, restored=False):
    runtime = no_reparse(runtime)
    # A package-local lock also protects the short directory swap used during
    # relocation. It never reaches into or controls the original package.
    with file_lock(RELEASE/'.portable-migration.lock', timeout=3):
        from portable_migration import recover_relocation, relocate_runtime
        if runtime == RELEASE/'user_data': recover_relocation(runtime)
        return _runtime_marker(runtime, restored=restored, relocate=relocate_runtime)

def _runtime_marker(runtime, *, restored=False, relocate=None):
    marker = runtime/'.daily-runtime.json'
    if marker.exists():
        m = read_json(marker)
        if m.get('magic') != MAGIC:
            raise RuntimeError('数据目录身份不匹配；未收养或改写此目录。')
        if Path(m['runtime']) != runtime or Path(m['release']) != RELEASE:
            return relocate(runtime,m)
        stat = runtime.stat()
        if m.get('directory_identity') != [stat.st_dev, stat.st_ino]: raise RuntimeError('数据目录已被替换，拒绝启动。')
        return m
    if runtime.exists() and any(runtime.iterdir()) and not restored:
        raise RuntimeError('非空数据目录缺少身份标记；不会自动接管旧库。')
    runtime.mkdir(parents=True, exist_ok=True)
    import uuid
    stat=runtime.stat()
    m={'magic':MAGIC, 'runtime':str(runtime), 'release':str(RELEASE), 'id':uuid.uuid4().hex,
       'directory_identity':[stat.st_dev,stat.st_ino], 'restored':restored,
       'portable':runtime==RELEASE/'user_data'}
    write_json(marker,m); return m

K=ctypes.WinDLL('kernel32',use_last_error=True)
K.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD]; K.OpenProcess.restype=W.HANDLE
K.CloseHandle.argtypes=[W.HANDLE]
K.GetProcessTimes.argtypes=[W.HANDLE,*([ctypes.POINTER(W.FILETIME)]*4)]
K.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,ctypes.POINTER(W.DWORD)]
K.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD]

def process_identity(pid):
    handle=K.OpenProcess(0x1000|0x100000,False,int(pid))
    if not handle:
        if ctypes.get_last_error() in (87,1168): return None
        raise RuntimeError('无法核验进程身份，未执行关闭操作。')
    try:
        if K.WaitForSingleObject(handle,0)==0: return None
        times=[W.FILETIME() for _ in range(4)]
        if not K.GetProcessTimes(handle,*[ctypes.byref(t) for t in times]):
            code=ctypes.get_last_error()
            if K.WaitForSingleObject(handle,0)==0: return None
            raise ctypes.WinError(code)
        buf=ctypes.create_unicode_buffer(32768); size=W.DWORD(len(buf))
        if not K.QueryFullProcessImageNameW(handle,0,buf,ctypes.byref(size)):
            code=ctypes.get_last_error()
            if K.WaitForSingleObject(handle,0)==0: return None
            raise ctypes.WinError(code)
        return {'process_id':int(pid),'creation_filetime':(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime,
                'executable_path':buf.value,'executable_sha256':sha(buf.value)}
    finally: K.CloseHandle(handle)

def command_line(pid):
    # A fixed script, numeric PID, structured JSON: no string-built shell code.
    p=subprocess.run([os.environ.get('SYSTEMROOT',r'C:\Windows')+r'\System32\WindowsPowerShell\v1.0\powershell.exe',
        '-NoProfile','-ExecutionPolicy','Bypass','-File',str(RELEASE/'tools'/'Inspect-Process.ps1'),'-ProcessId',str(int(pid))],
        capture_output=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW,timeout=15)
    if p.returncode: raise RuntimeError('无法读取实际进程命令；未执行关闭。'+p.stderr)
    return json.loads(p.stdout.lstrip('\ufeff'))

def listeners(port):
    # Windows IPv4 owner table: exact listener ownership, no reliance on historical PIDs.
    class Row(ctypes.Structure):
        _fields_=[(n,W.DWORD) for n in ('state','addr','port','remote','remoteport','pid')]
    api=ctypes.WinDLL('iphlpapi').GetExtendedTcpTable
    size=W.DWORD(0); api(None,ctypes.byref(size),False,2,5,0)
    buf=ctypes.create_string_buffer(size.value)
    if api(buf,ctypes.byref(size),False,2,5,0): raise RuntimeError('不能核对监听端口。')
    count=W.DWORD.from_buffer(buf).value; found=[]
    for i in range(count):
        r=Row.from_buffer(buf,4+i*ctypes.sizeof(Row))
        if r.state==2 and socket.ntohs(r.port & 65535)==int(port):
            found.append({'pid':r.pid,'address':socket.inet_ntoa(int(r.addr).to_bytes(4,'little'))})
    return found

def request(port,path,token=None,method='GET'):
    import urllib.request, urllib.error
    headers={'X-Labimage-Control-Token':token} if token else {}
    req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,headers=headers,method=method,data=b'' if method=='POST' else None)
    try:
        with urllib.request.urlopen(req,timeout=8) as r: return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError('服务拒绝此操作：'+e.read().decode('utf-8',errors='replace')) from e
