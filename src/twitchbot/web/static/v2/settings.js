"use strict";
(()=>{
  let draft=null,requestId=null,restoreId=null;
  const labels={local_ready:"本体に保存・NASへの転送待ち",transfer_failed:"本体に保存・NASへの転送を再試行します",nas_verified:"NASへの保存を確認済み",retiring:"保存期限を過ぎたコピーを整理中",expired:"保存期限により整理済み"};
  const message=code=>({revision_conflict:"別の変更があります。画面を読み直して確認してください。",backup_queue_full:"バックアップの待機件数が上限に達しています。",operations_unavailable:"記録先と接続設定がまだ準備されていません。",invalid_cursor:"保存結果が更新されました。先頭から読み直してください。"}[code]||"処理を完了できませんでした。入力を残しています。");
  const date=value=>new Intl.DateTimeFormat("ja-JP",{timeZone:"Asia/Tokyo",dateStyle:"short",timeStyle:"short"}).format(new Date(value))+" JST";
  const post=async(path,body)=>{const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}),d=await r.json();if(!r.ok)throw Error(d.error);return d;};

  function mount(root){
    const find=id=>root.querySelector(`#${id}`),status=find("settings-status"),form=find("backup-policy"),toggle=find("recording-toggle"),manual=find("manual-backup");
    const restoreForm=find("restore-candidate-form"),restoreSelect=find("restore-backup"),controls=find("backup-list-controls"),more=find("backup-more"),listStatus=find("backup-list-status");
    if(!status||!form||!toggle||!manual||!restoreForm||!restoreSelect||!controls||!more)return null;
    const lifecycle=new AbortController(),signal=lifecycle.signal;
    let active=true,state=null,dirty=Boolean(draft),busy=false,refreshing=false,listRefreshing=false,backupCursor=null,loadedItems=[];
    const listen=(target,type,handler)=>target.addEventListener(type,handler,{signal});
    const lock=value=>{busy=value;restoreForm.querySelectorAll("select,button").forEach(el=>el.disabled=value||!state?.enabled);form.querySelectorAll("input,select,button").forEach(el=>el.disabled=value);toggle.disabled=value||!state?.runtime.ready;manual.disabled=value||!state?.enabled;};
    const rememberDraft=()=>{draft={enabled:form.elements.enabled.checked,daily_hour:form.elements.daily_hour.value,revision:form.dataset.revision};dirty=true;};
    const applyDraft=()=>{if(draft){form.elements.enabled.checked=draft.enabled;form.elements.daily_hour.value=draft.daily_hour;form.dataset.revision=draft.revision;}};
    const listQuery=cursor=>{const query=new URLSearchParams({sort:controls.elements.sort.value,order:controls.elements.order.value,limit:"50"});if(controls.elements.state.value)query.set("state",controls.elements.state.value);if(cursor)query.set("cursor",cursor);return `/api/v2/backups?${query}`;};
    const renderBackups=()=>{
      const rows=loadedItems.map(item=>{const tr=document.createElement("tr");for(const text of [date(item.created_at),labels[item.state]||"状態を確認中",item.state==="expired"?"削除済み":`${(item.size_bytes/1024**2).toFixed(2)} MiB`]){const td=document.createElement("td");td.textContent=text;tr.append(td);}return tr;});
      find("backup-items").replaceChildren(...rows);more.hidden=!backupCursor;more.disabled=listRefreshing;
      listStatus.textContent=loadedItems.length?`${loadedItems.length}件を表示しています。${backupCursor?"続きがあります。":"すべて表示しました。"}`:"該当する保存結果はありません。";
    };
    const refreshBackups=async({append=false}={})=>{
      if(listRefreshing)return;listRefreshing=true;more.disabled=true;
      try{
        const r=await fetch(listQuery(append?backupCursor:null),{cache:"no-store",signal}),data=await r.json();if(!r.ok)throw Error(data.error);if(!active)return;
        if(append){const ids=new Set(loadedItems.map(item=>item.id));loadedItems.push(...data.items.filter(item=>!ids.has(item.id)));}else loadedItems=data.items;
        backupCursor=data.next_cursor;renderBackups();
      }catch(error){if(error.name!=="AbortError"&&active)listStatus.textContent=message(error.message);}
      finally{listRefreshing=false;if(active)more.disabled=!backupCursor;}
    };
    const refresh=async()=>{
      if(busy||refreshing)return;refreshing=true;
      try{
        const r=await fetch("/api/v2/operations",{cache:"no-store",signal}),data=await r.json();if(!r.ok)throw Error(data.error);if(busy||!active)return;state=data;
        find("recording-status").textContent=data.enabled?"記録を有効にしています。":"記録は停止中です。";toggle.textContent=data.enabled?"記録を停止":"記録を開始";toggle.disabled=!data.runtime.ready;
        const connected=data.connection.state==="ready";
        find("connection-status").textContent=connected?(data.connection.presets?"Twitchに接続済みです。":"読み取りは接続済みです。配信操作には追加の認証が必要です。"):data.connection.state==="not_validated"?"記録開始後にTwitch接続を確認します。":"Twitch接続を確認できません。認証の有効期限と権限を確認してください。";
        find("nas-status").textContent=data.backups.nas_configured?"NASへの保存先が設定されています。転送結果は下の一覧で確認できます。":"NASの保存先が未設定です。本体のコピー作成は利用できます。";
        if(!dirty){form.elements.enabled.checked=data.backup_policy.enabled;form.elements.daily_hour.value=String(data.backup_policy.daily_hour);form.dataset.revision=String(data.backup_policy.revision);}else applyDraft();
        form.querySelectorAll("input,select,button").forEach(el=>el.disabled=false);manual.disabled=!data.enabled;
        find("backup-state").textContent=(["degraded","deferred"].includes(data.backups.state)||data.backups.transfer_error)?"バックアップに問題があります。本体の保存先、空き容量、NAS接続を確認してください。":data.enabled?"保存結果は自動で更新されます。":"記録の再開後にバックアップ処理を再開します。";
        const chosen=restoreSelect.value,eligible=(data.backups.items||[]).filter(item=>["local_ready","transfer_failed","nas_verified"].includes(item.state));
        restoreSelect.replaceChildren(new Option("コピーを選ぶ",""),...eligible.map(item=>new Option(`${date(item.created_at)} · ${labels[item.state]||"状態を確認中"}`,item.id)));
        restoreSelect.value=eligible.some(item=>item.id===chosen)?chosen:"";
        restoreForm.querySelectorAll("select,button").forEach(el=>el.disabled=!data.enabled);
        const restoreJob=restoreId?data.restore_jobs.find(job=>job.id===restoreId):data.restore_jobs[0];
        if(restoreJob)find("restore-result").textContent=({pending:"復元候補の作成を受け付けました。",running:"バックアップと復元候補を検証しています。",verified:"停止済みの復元候補を作成・検証しました。現在のデータはそのまま使用できます。",failed:"復元候補を検証できませんでした。コピー・空き容量・NAS接続を確認してください。",unknown:"前の作成結果を確認できません。候補を自動で有効化していません。"}[restoreJob.state]);
        if(requestId){const job=data.jobs.find(item=>item.id===requestId);if(job&&!["pending","running"].includes(job.state)){find("backup-result").textContent=job.state==="succeeded"?"本体にコピーを作成しました。NASの結果は保存一覧で確認できます。":"コピー作成の結果を確認できません。保存先と空き容量を確認してください。";requestId=null;await refreshBackups();}}
      }catch(error){if(error.name!=="AbortError"&&active)status.textContent=message(error.message);}
      finally{refreshing=false;}
    };
    listen(form,"input",rememberDraft);
    listen(form,"submit",async event=>{event.preventDefault();if(busy)return;lock(true);status.textContent="";try{await post("/api/v2/backup-policy",{enabled:form.elements.enabled.checked,daily_hour:Number(form.elements.daily_hour.value),revision:Number(form.dataset.revision)});draft=null;dirty=false;}catch(error){if(active)status.textContent=message(error.message);}finally{if(active){lock(false);if(!dirty)await refresh();}}});
    listen(toggle,"click",async()=>{if(!state||busy)return;lock(true);status.textContent="";try{await post("/api/v2/recording-setting",{enabled:!state.enabled,revision:state.revision});}catch(error){if(active)status.textContent=message(error.message);}finally{if(active){lock(false);await refresh();}}});
    listen(manual,"click",async()=>{if(busy)return;lock(true);status.textContent="";requestId||=Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,"0")).join("");try{await post("/api/v2/backups",{request_id:requestId});if(active)find("backup-result").textContent="バックアップを受け付けました。";}catch(error){if(active)find("backup-result").textContent=message(error.message);}finally{if(active){lock(false);await refresh();}}});
    listen(restoreSelect,"change",()=>{restoreId=null;});
    listen(restoreForm,"submit",async event=>{event.preventDefault();if(busy||!restoreSelect.value)return;lock(true);restoreId||=Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,"0")).join("");try{await post("/api/v2/restore-candidates",{request_id:restoreId,backup_id:restoreSelect.value});if(active)find("restore-result").textContent="復元候補の検証を受け付けました。";}catch(error){if(active)status.textContent=message(error.message);}finally{if(active){lock(false);await refresh();}}});
    listen(controls,"change",()=>{backupCursor=null;loadedItems=[];refreshBackups();});listen(more,"click",()=>refreshBackups({append:true}));
    listen(document,"visibilitychange",()=>{if(document.visibilityState==="visible"){refresh();refreshBackups();}});
    refresh();refreshBackups();const interval=setInterval(()=>{if(document.visibilityState==="visible")refresh();},10000);
    return()=>{active=false;clearInterval(interval);lifecycle.abort();};
  }

  if(window.IniwaApp)window.IniwaApp.register("settings",mount);else document.addEventListener("DOMContentLoaded",()=>mount(document),{once:true});
})();
