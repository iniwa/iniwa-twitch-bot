"""One local-only initial setup window for isolated, real Twitch login checks."""

import argparse
import atexit
import json
import logging
import os
from pathlib import Path
from threading import Lock
from uuid import uuid4

from flask import jsonify, redirect, request
from werkzeug.serving import make_server

from twitchbot.adapters.oauth import OAuthAccount, TwitchOAuth
from twitchbot.application.workers import ProcessLease
from twitchbot.application.persistence import PersistenceError
from twitchbot.bootstrap import build_container, from_file
from twitchbot.web.app import create_app as create_v2_app
from twitchbot.web.community import require_local_json


PAGE = '''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Twitch 初回設定</title>
<style>body{font:16px/1.8 system-ui;max-width:660px;margin:48px auto;padding:24px;background:#f4f3f8;color:#201c30}form{padding:24px;background:white;border:1px solid #ddd;border-radius:12px}input,select{box-sizing:border-box;width:100%;padding:12px;margin:12px 0}button{padding:12px 20px;background:#6441a5;color:white;border:0;border-radius:8px}p{overflow-wrap:anywhere}</style>
<h1>Twitchログインの初回設定</h1><p>新版専用のアプリをTwitch Developersで登録し、Client IDを入力してください。Client TypeはSecret不要のPublicをおすすめします。</p>
<p><a href="https://dev.twitch.tv/console/apps" target="_blank" rel="noopener noreferrer">Twitch Developersを開く</a> → Register Your Application → 名前（例: Iniwa Stream Dashboard）・カテゴリー（Application Integration）・Client Type（Public）を設定します。Redirect URL欄には http://localhost:3000 を登録できます。このログイン方式ではリダイレクトを使用しません。</p>
<p>この画面は新版の独立した接続確認用です。現在稼働中のBotの設定や配信データは変更しません。</p>
<form id="setup"><label for="client-id">新しいアプリのClient ID</label><input id="client-id" autocomplete="off" required maxlength="128">
<label for="client-type">登録したClient Type</label><select id="client-type"><option value="public">Public（Secret不要）</option><option value="confidential">Confidential（Secretが必要）</option></select>
<div id="secret-field" hidden><label for="secret">同じアプリのClient Secret</label><input id="secret" type="password" autocomplete="off" maxlength="256" disabled></div>
<button>初期設定を保存してログインへ</button><p id="result" role="status"></p></form>
<p>保存後、配信者アカウントでTwitchにログインして接続を確認します。チャット投稿用Botの認証は後から追加できます。Secretやトークンを会話に貼る必要はありません。</p>
<script>
const type=document.getElementById('client-type'),secret=document.getElementById('secret');
type.addEventListener('change',()=>{const required=type.value==='confidential';document.getElementById('secret-field').hidden=!required;secret.disabled=!required;secret.required=required;secret.value='';});
const errors={origin_rejected:'このブラウザーからの保存要求を確認できませんでした。画面と同じURLを通常のブラウザーで開き直してください。',invalid_request:'入力を読み取れませんでした。画面を再読み込みしてください。',invalid_application:'Client ID・Client Typeを確認してください。Confidentialの場合は同じアプリのSecretが必要です。',application_verification_failed:'Twitchがアプリ情報を受け付けませんでした。Client IDとSecretが同じアプリのものか確認してください。',oauth_unavailable:'Twitchに接続できませんでした。接続状態を確認し、時間をおいて再試行してください。',setup_failed:'このPCへの設定保存・起動に失敗しました。原因確認が必要です。',setup_busy:'設定処理中です。少し待ってから再試行してください。',setup_already_complete:'設定済みです。画面を再読み込みするとログインへ進みます。'};
document.getElementById('setup').addEventListener('submit',async e=>{e.preventDefault();const button=e.target.querySelector('button'),result=document.getElementById('result');button.disabled=true;result.textContent='初期設定を保存しています。';try{const body={client_id:document.getElementById('client-id').value.trim(),client_type:type.value};if(type.value==='confidential')body.client_secret=secret.value.trim();secret.value='';const r=await fetch('/api/v2/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await r.json();if(!r.ok){result.textContent=errors[data.error]||errors.setup_failed;return;}location.replace('/v2/connect');}catch(e){result.textContent='初期設定画面のサーバーに接続できませんでした。画面を再読み込みしてください。';}finally{button.disabled=false;}});
</script></html>'''


def write_private(path, value):
    descriptor=os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(descriptor,'w',encoding='utf-8') as output:
        json.dump(value,output);output.flush();os.fsync(output.fileno())


def protect_local_app(app):
    @app.before_request
    def local_only():
        if request.remote_addr not in ('127.0.0.1','::1') or request.host.split(':')[0] not in ('127.0.0.1','localhost'):
            return '',403

    @app.after_request
    def private_response(response):
        response.headers['Cache-Control']='no-store'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Content-Type-Options']='nosniff'
        return response


def resume_app(root):
    """Resume only the explicitly chosen setup area; never replace its config."""
    root=Path(root)
    if not root.is_absolute() or root.resolve()!=root or not root.is_dir():
        raise ValueError('An explicit local verification directory is required')
    lease=ProcessLease(root/'.setup.lock');lease.acquire()
    container=None
    try:
        container=from_file(root/'private/runtime.json')
        app=create_v2_app(container)
        protect_local_app(app)

        @app.get('/')
        def home():
            return redirect('/v2/connect')

        @app.get('/api/v2/setup')
        def status():
            return jsonify(configured=True,error=None)

        def close():
            container.runtime.stop()
            lease.release()
        app.extensions['login_setup.close']=close
        container.runtime.start()
        return app
    except Exception:
        if container is not None: container.runtime.stop()
        lease.release()
        raise


def create_app(identity, root, *, transport=None):
    root=Path(root)
    if not root.is_absolute() or root.resolve()!=root or not root.is_dir() or any(root.iterdir()):
        raise ValueError('An empty, explicit local verification directory is required')
    if not isinstance(identity,dict) or set(identity)-{'client_id','broadcaster_id','bot_user_id'}:
        raise ValueError('Invalid identity metadata')
    # Validate public identifiers without looking for any other configuration.
    OAuthAccount(identity.get('client_id'),identity.get('broadcaster_id'))
    if identity.get('bot_user_id'): OAuthAccount(identity['client_id'],identity['bot_user_id'])
    lease=ProcessLease(root/'.setup.lock');lease.acquire()
    private=root/'private';private.mkdir(mode=0o700)
    grants=private/'grants';grants.mkdir(mode=0o700)
    staging=root/'backups';staging.mkdir()
    app=create_v2_app();guard=Lock();configured=False;last_error=None
    api=TwitchOAuth(transport)

    def failure(code, status):
        nonlocal last_error
        last_error=code
        return jsonify(error=code),status

    protect_local_app(app)

    @app.get('/')
    def setup_page():
        return redirect('/v2/connect') if configured else PAGE

    @app.get('/api/v2/setup')
    def setup_status():
        return jsonify(configured=configured,error=last_error)

    @app.post('/api/v2/setup')
    def setup():
        nonlocal configured,last_error
        try:
            body=require_local_json()
        except PersistenceError as exc:
            return failure('origin_rejected' if exc.code=='origin_rejected' else 'invalid_request',403 if exc.code=='origin_rejected' else 400)
        try:
            if not guard.acquire(blocking=False): return failure('setup_busy',409)
            created=[]
            container=None
            try:
                if configured: return failure('setup_already_complete',409)
                client_id=body.get('client_id')
                client_type=body.get('client_type')
                secret=body.get('client_secret')
                try:
                    OAuthAccount(client_id,identity['broadcaster_id'],client_type,secret)
                    if client_type=='public' and secret is not None: raise ValueError
                except (PersistenceError,ValueError):
                    return failure('invalid_application',400)
                # Verify the application secret once. Discard the app token;
                # user grants still require explicit Twitch login/consent.
                if client_type=='confidential':
                    try:
                        reply=api.call('token',{'client_id':client_id,'client_secret':secret,'grant_type':'client_credentials'})
                    except Exception:
                        return failure('oauth_unavailable',503)
                    if reply.status==429 or reply.status>=500: return failure('oauth_unavailable',503)
                    if reply.status!=200 or not isinstance(reply.data,dict) or not isinstance(reply.data.get('access_token'),str) or not reply.data['access_token']:
                        return failure('application_verification_failed',400)
                # Public registration is checked by explicit device login later.
                accounts={'broadcaster':dict(client_id=client_id,user_id=identity['broadcaster_id'],client_type=client_type)}
                if secret is not None: accounts['broadcaster']['client_secret']=secret
                oauth={'private_root':str(grants),'accounts':accounts}
                container=build_container(root/'candidate.sqlite3',staging,None,identity['broadcaster_id'],
                    oauth_configuration=oauth,oauth_transport=transport,transport=transport,bot_transport=transport)
                write_private(private/'oauth-application.json',oauth)
                created.append(private/'oauth-application.json')
                write_private(private/'runtime.json',dict(database_path=str(root/'candidate.sqlite3'),staging_root=str(staging),channel_id=identity['broadcaster_id'],oauth_file=str(private/'oauth-application.json')))
                created.append(private/'runtime.json')
                container.runtime.start()
                app.extensions['twitchbot.container']=container
                configured=True
                last_error=None
                return jsonify(state='configured')
            except Exception:
                if container is not None: container.runtime.stop()
                for path in created:
                    # Only files this attempt created, below its checked area.
                    if path.parent == private and not path.is_symlink(): path.unlink(missing_ok=True)
                raise
            finally:guard.release()
        except Exception:
            # Never serialize submitted form values or transport exception text.
            return failure('setup_failed',500)

    def close():
        app.extensions['twitchbot.container'].runtime.stop()
        lease.release()
    app.extensions['login_setup.close']=close
    return app


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--identity-file',type=Path)
    mode.add_argument('--resume',action='store_true',help='Resume the saved runtime in the explicit work root')
    parser.add_argument('--work-root',required=True,type=Path)
    parser.add_argument('--port',default=0,type=int)
    args=parser.parse_args()
    if not 0<=args.port<=65535: parser.error('Invalid port')
    if args.resume:
        app=resume_app(args.work_root)
    else:
        if not args.identity_file.is_absolute() or args.identity_file.is_symlink() or args.identity_file.stat().st_size>4096:
            raise SystemExit('Invalid explicit identity file')
        app=create_app(json.loads(args.identity_file.read_text(encoding='utf-8')),args.work_root)
    logging.getLogger('werkzeug').disabled=True
    atexit.register(app.extensions['login_setup.close'])
    try:
        server=make_server('127.0.0.1',args.port,app,threaded=True)
        metadata=args.work_root/('.server-'+uuid4().hex+'.json')
        try:
            write_private(metadata,{'url':'http://127.0.0.1:'+str(server.server_port),'pid':os.getpid()})
            os.replace(metadata,args.work_root/'server.json')
        finally:metadata.unlink(missing_ok=True)
        server.serve_forever()
    finally:app.extensions['login_setup.close']()
