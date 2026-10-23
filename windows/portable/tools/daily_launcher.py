"""Chinese BAT launcher backend. All state is under an explicit owned runtime."""
from __future__ import annotations
import argparse, json, os, secrets, socket, subprocess, sys, time, traceback, uuid, webbrowser
from pathlib import Path
from daily_common import (RELEASE,read_json,write_json,sha,no_reparse,file_lock,service_locked,
    verify_manifest,runtime_marker,process_identity,command_line,listeners,request)

def profile(path):
    cfg=read_json(path)
    runtime=Path(cfg['runtime'])
    if not runtime.is_absolute(): runtime=RELEASE/runtime
    runtime=no_reparse(runtime)
    port=int(cfg.get('port',8776))
    if not 1024<=port<=65535 or port in (8774,8775): raise RuntimeError('日常版不使用旧服务端口8774/8775。')
    return cfg,runtime,port

def current(runtime):
    path=runtime/'records'/'current.json'
    if not path.exists():
        if service_locked(runtime): raise RuntimeError('服务正在运行但会话记录缺失；拒绝接管或停止，请查看日志。')
        return None
    try:
        rec=read_json(path)
        if rec['runtime']!=str(runtime) or rec['release']!=str(RELEASE) or rec['schema']!='labimage-daily-session-v1': raise ValueError('运行根不一致')
        ident=rec['service']; live=process_identity(ident['process_id'])
        if live is None or live['creation_filetime']!=ident['creation_filetime']:
            if service_locked(runtime): raise ValueError('会话记录与正在占用目录的进程不符')
            return None
        for k in ('executable_path','executable_sha256'):
            if live[k]!=ident[k]: raise ValueError('进程可执行文件不符')
        if command_line(ident['process_id'])!=ident['command_line']: raise ValueError('实际进程命令不符')
        if ident['command_line']!=subprocess.list2cmdline(rec['arguments']): raise ValueError('命令未绑定本版模块和会话文件')
        if listeners(rec['port'])!=[{'pid':ident['process_id'],'address':'127.0.0.1'}]: raise ValueError('监听归属不符')
        st=request(rec['port'],'/api/daily/identity',rec['control_token'])
        expected={'session_id':rec['session_id'],'runtime_directory':str(runtime),
            'source_manifest_sha256':rec['manifest_sha256'],'process_id':ident['process_id'],
            'source_directory':str(RELEASE/'release_source'),'data_directory':str(runtime/'data'),
            'mode':rec['mode'],'feature_cache_enabled':rec['mode']=='accelerated',
            'detector_class':'app.feature_cache.'+('CachedDetector' if rec['mode']=='accelerated' else 'UncachedDetector'),
            'application_version':'0.3.6-feature-cache-stable-trial','cache_version':'1.1.3'}
        for k,v in expected.items():
            if st.get(k)!=v: raise ValueError('HTTP身份不符：'+k)
        if not service_locked(runtime): raise ValueError('进程未持有数据目录锁')
        return rec
    except Exception as e:
        raise RuntimeError('会话记录无效或服务归属未能核实；未启动第二份，也未关闭任何进程。'+str(e)) from e

def stop(runtime):
    rec=current(runtime)
    if not rec:
        print('图鉴已关闭；资料完整保留。'); return {'already_stopped':True}
    result=request(rec['port'],'/api/daily/shutdown',rec['control_token'],'POST')
    end=time.monotonic()+75
    while time.monotonic()<end:
        ident=process_identity(rec['service']['process_id'])
        if ident is None or ident['creation_filetime']!=rec['service']['creation_filetime']:
            if not service_locked(runtime) and not listeners(rec['port']):
                info={'stopped':True,'session_id':rec['session_id'],'service':rec['service'],
                    'admission_result':result,'ended_at':time.time(),'method':'authenticated atomic admission + cooperative Uvicorn exit; no kill'}
                write_json(runtime/'records'/(rec['session_id']+'.stop.json'),info)
                print('图鉴已正常关闭，监听已释放；任务、图库、备注和缓存均保留。'); return info
        time.sleep(.2)
    raise RuntimeError('服务尚未完成退出，未强制停止。请查看日志并稍后再次关闭。')

def start(runtime,port,mode,digest,controller=None):
    rec=current(runtime)
    if rec:
        if rec['manifest_sha256']!=digest: raise RuntimeError('运行版本与交付清单不符，请先正常关闭后重新启动。')
        if rec['port']!=port: raise RuntimeError('同一数据目录已在记录端口运行，配置端口不一致。')
        if rec['mode']==mode:
            print('已经运行，复用同一图鉴服务。'); return rec
        print('正在正常关闭空闲服务，并以新进程切换模式（保留同一数据目录）……')
        stop(runtime)
    if service_locked(runtime): raise RuntimeError('本数据目录已有服务占用，拒绝重复启动。')
    # Test exclusive bind, covering both unknown IPv4 and conflicting IPv6 sockets.
    try:
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1); s.bind(('127.0.0.1',port))
    except OSError as e: raise RuntimeError(f'端口 {port} 已被其他程序占用；未停止陌生进程，也未更换空数据库。请关闭占用程序或联系维护人。') from e
    sid=uuid.uuid4().hex
    for name in ('logs','records'): (runtime/name).mkdir(exist_ok=True)
    session_path=runtime/'records'/(sid+'.session.json')
    args=[sys.executable,'-B','-X','utf8',str(RELEASE/'tools'/'daily_server.py'),'--session-file',str(session_path)]
    rec={'schema':'labimage-daily-session-v1','session_id':sid,'control_token':secrets.token_urlsafe(40),
        'runtime':str(runtime),'release':str(RELEASE),'mode':mode,'port':port,
        'manifest_sha256':digest,'application_version':'0.3.6-feature-cache-stable-trial','cache_version':'1.1.3',
        'arguments':args,'requested_at':time.time(),'python':sys.executable,
        'stdout':str(runtime/'logs'/(sid+'.stdout.log')),'stderr':str(runtime/'logs'/(sid+'.stderr.log'))}
    if controller:
        rec['controller']={key:controller[key] for key in ('controller_id','port','control_token')}
    write_json(session_path,rec)
    env=os.environ.copy(); env['PYTHONUTF8']='1'; env['PYTHONDONTWRITEBYTECODE']='1'
    # Do not inherit user algorithm/cache tuning. Same qualified defaults for every session.
    for k in list(env):
        if k.startswith('LABIMAGE_'): env.pop(k)
    with open(rec['stdout'],'wb') as out,open(rec['stderr'],'wb') as err:
        child=subprocess.Popen(args,cwd=RELEASE/'release_source',env=env,stdout=out,stderr=err,
            creationflags=subprocess.CREATE_NO_WINDOW,close_fds=True)
    write_json(runtime/'records'/(sid+'.attempt.json'),dict(rec,launcher_pid=child.pid))
    end=time.monotonic()+50; last='等待服务就绪'
    while time.monotonic()<end:
        try:
            owned=current(runtime)
            if owned and owned['session_id']==sid:
                status=request(port,'/api/status')
                if status['active_jobs']!=0: raise RuntimeError('新启动时任务尚未稳定')
                print('图鉴已就绪：'+f'http://127.0.0.1:{port}'); return owned
        except Exception as e: last=str(e)
        if child.poll() is not None: break
        time.sleep(.25)
    write_json(runtime/'records'/(sid+'.startup-failure.json'),{'error':last,'returncode':child.poll(),'stderr':rec['stderr'],'time':time.time()})
    raise RuntimeError('启动失败或身份检查未通过；未强行结束任何进程。'+last+'\n日志：'+rec['stderr'])

def main():
    p=argparse.ArgumentParser(); p.add_argument('action',choices=['start','stop','backup','restore','status'])
    p.add_argument('--profile',default=str(RELEASE/'daily_profile.json')); p.add_argument('--archive'); p.add_argument('--target')
    args=p.parse_args(); digest=verify_manifest(); cfg,runtime,port=profile(args.profile)
    if args.action=='restore':
        from daily_backup import restore_package
        if not args.archive: raise RuntimeError('请选择本版备份ZIP文件。')
        target=Path(args.target) if args.target else RELEASE.parent/('图鉴_恢复_'+time.strftime('%Y%m%d_%H%M%S'))
        result=restore_package(Path(args.archive),target,RELEASE)
        print('已恢复到全新目录：'+str(target)+'\n请双击：'+str(target/'启动图鉴.bat'))
        return
    runtime_marker(runtime)
    from portable_controller import (ensure_controller, current_controller,
        stop_controller, preferred_mode, remember_mode)
    # Controller shutdown must precede taking operation.lock: a mode switch
    # owns that lock through the entire backend handover. Busy => reject now.
    if args.action=='stop':
        stop_controller(runtime)
    with file_lock(runtime/'operation.lock',timeout=3):
        if args.action=='start':
            controller=ensure_controller(runtime,port,digest)
            rec=start(runtime,port,preferred_mode(runtime),digest,controller)
            remember_mode(runtime,rec['mode'])
            if os.environ.get('LABIMAGE_NO_BROWSER')!='1': webbrowser.open(f'http://127.0.0.1:{rec["port"]}')
            print('数据：'+str(runtime/'data')+'\n模式：'+('加速模式' if rec['mode']=='accelerated' else '兼容模式（缓存关闭）'))
        elif args.action=='stop': stop(runtime)
        elif args.action=='status': print(json.dumps({'backend':current(runtime),'controller':current_controller(runtime)},ensure_ascii=False,indent=2))
        elif args.action=='backup':
            if current_controller(runtime) or current(runtime) or service_locked(runtime): raise RuntimeError('请先双击“关闭图鉴”，确认关闭后再备份。本版只做离线备份，不会自动强停。')
            from daily_backup import backup
            # Lifetime lock blocks server startup throughout the snapshot/file-copy window.
            with file_lock(runtime/'service.lock'):
                result=backup(runtime,RELEASE/'backups',RELEASE)
            write_json(runtime/'records'/'last_backup.json',result)
            print('备份完成：'+json.dumps(result,ensure_ascii=False))

if __name__=='__main__':
    try: main()
    except Exception as e:
        text='操作未完成：'+str(e)
        print(text,file=sys.stderr)
        (RELEASE/'launcher_errors.log').open('a',encoding='utf-8').write(time.strftime('%Y-%m-%d %H:%M:%S')+' '+text+'\n'+traceback.format_exc()+'\n')
        sys.exit(1)
