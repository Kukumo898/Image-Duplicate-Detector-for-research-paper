"""One owned Uvicorn process. Application shutdown is cooperative, never kill()."""
from __future__ import annotations
import argparse, ctypes, os, sys, time
from pathlib import Path
from daily_common import RELEASE, file_lock, read_json, write_json, verify_manifest, process_identity

def main():
    p=argparse.ArgumentParser(); p.add_argument('--session-file',required=True); args=p.parse_args()
    session=read_json(args.session_file); runtime=Path(session['runtime'])
    with file_lock(runtime/'service.lock'):
        digest=verify_manifest()
        if digest != session['manifest_sha256']: raise RuntimeError('源码清单在启动期间变化')
        os.environ.update({'LABIMAGE_RUNTIME':str(runtime),'LABIMAGE_DATA':str(runtime/'data'),
            'LABIMAGE_FEATURE_CACHE_DIR':str(runtime/'cache'),'LABIMAGE_FEATURE_CACHE':'1' if session['mode']=='accelerated' else '0',
            'LABIMAGE_SESSION_ID':session['session_id'],'LABIMAGE_CONTROL_TOKEN':session['control_token'],
            'LABIMAGE_SOURCE_MANIFEST_SHA256':digest,'PYTHONDONTWRITEBYTECODE':'1','WEB_CONCURRENCY':'1'})
        for key in ('UVICORN_RELOAD','UVICORN_WORKERS','UVICORN_UDS','UVICORN_FD','UVICORN_ENV_FILE'): os.environ.pop(key,None)
        sys.path.insert(0,str(RELEASE/'release_source'))
        import uvicorn
        from app.main import app
        config=uvicorn.Config(app,host='127.0.0.1',port=session['port'],workers=1,access_log=True,timeout_graceful_shutdown=60)
        server=uvicorn.Server(config)
        app.state.daily_shutdown_callback=lambda:setattr(server,'should_exit',True)
        ident=process_identity(os.getpid())
        ctypes.windll.kernel32.GetCommandLineW.restype=ctypes.c_wchar_p
        ident['command_line']=ctypes.windll.kernel32.GetCommandLineW()
        session['service']=ident
        session['started_at']=time.time()
        write_json(runtime/'records'/'current.json',session)
        write_json(runtime/'records'/(session['session_id']+'.launch.json'),session)
        server.run()
        # Uvicorn has drained accepted HTTP responses and completed lifespan.
        from app.main import POOL
        POOL.shutdown(wait=True)
        import sqlite3
        with sqlite3.connect(runtime/'data'/'labimage.sqlite3') as con: con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        write_json(runtime/'records'/(session['session_id']+'.exit.json'),{'pid':os.getpid(),'ended_at':time.time(),'method':'Uvicorn should_exit; drain HTTP; lifespan; worker pool join; WAL checkpoint'})

if __name__=='__main__': main()
