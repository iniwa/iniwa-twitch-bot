"use strict";
(()=>{
  let savedPreset=null,savedPolicy=null;
  const names={start:"開始",lock:"受付終了",resolve:"結果確定",cancel:"取り消し"};
  const states={ACTIVE:"受付中",LOCKED:"受付終了・正解の確定待ち",RESOLVED:"確定済み",CANCELED:"取り消し済み"};
  const results={pending:"受付済み",dispatching:"結果を確認中",succeeded:"確認済み",failed:"失敗・内容を再確認してください",unknown:"結果不明・再送しません",expired:"期限切れ"};
  const stamp=value=>value?`${new Intl.DateTimeFormat("ja-JP",{timeZone:"Asia/Tokyo",dateStyle:"short",timeStyle:"short"}).format(new Date(value))} JST`:"更新日時不明";
  const post=async(path,body)=>{const response=await fetch(`/api/v2/predictions/${path}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}),data=await response.json();if(!response.ok)throw Error(data.error);return data;};

  function mount(root){
    const find=id=>root.querySelector(`#${id}`),form=find("prediction-preset"),policy=find("prediction-policy"),select=find("prediction-preset-select"),status=find("prediction-status"),winner=find("prediction-winner"),controls=find("prediction-list-controls");
    if(!form||!policy||!select||!status||!winner||!controls)return null;
    const lifecycle=new AbortController(),signal=lifecycle.signal;
    let active=true,state=null,selected=null,preview=null,current=null,busy=false,refreshing=false,dirty=Boolean(savedPreset),policyDirty=Boolean(savedPolicy);
    const listen=(target,type,handler)=>target.addEventListener(type,handler,{signal});
    const fail=error=>{if(active)status.textContent=({revision_conflict:"別の変更があります。プリセットを選び直してください。",preview_changed:"保存内容が変わりました。操作内容を確認し直してください。",prediction_state_unavailable:"Twitchの状態を確認できません。接続と権限を確認して、状態を再確認してください。",prediction_start_unavailable:"新規開始は有効な配信中に利用できます。",prediction_already_active_or_unknown:"進行中または開始結果が不明な予想があります。",predictions_unavailable:"接続設定がまだ準備されていません。"}[error.message]||"操作できませんでした。入力と予想の状態を確認してください。");};
    const dismiss=()=>{preview=null;find("prediction-confirmation").hidden=true;};
    const buttons=()=>{root.querySelectorAll("[data-action]").forEach(button=>button.disabled=busy||!state?.fresh||!current||(button.dataset.action==="lock"?current.status!=="ACTIVE":button.dataset.action==="resolve"?current.status!=="LOCKED":!["ACTIVE","LOCKED"].includes(current.status)));find("prediction-start").disabled=busy||dirty||!selected||!state?.fresh||!state?.policy.enabled;};
    const lock=value=>{busy=value;root.querySelectorAll("input,textarea,select,button").forEach(element=>element.disabled=value);if(!value)buttons();};
    const capturePreset=()=>{savedPreset={selected,values:{name:form.elements.name.value,title:form.elements.title.value,outcomes:form.elements.outcomes.value,prediction_window:form.elements.prediction_window.value}};dirty=true;dismiss();buttons();};
    const capturePolicy=()=>{savedPolicy={enabled:policy.elements.enabled.checked,revision:policy.dataset.revision};policyDirty=true;};
    const choose=id=>{selected=state?.presets.find(item=>item.id===id)||null;savedPreset=null;dirty=false;dismiss();form.reset();if(selected){form.elements.name.value=selected.name;form.elements.title.value=selected.specification.title;form.elements.outcomes.value=selected.specification.outcomes.join("\n");form.elements.prediction_window.value=selected.specification.prediction_window;}find("prediction-preset-updated").textContent=selected?`更新: ${stamp(selected.updated_at)}`:"";buttons();};
    const restorePreset=()=>{if(!savedPreset)return false;selected=savedPreset.selected;form.reset();for(const [key,value] of Object.entries(savedPreset.values))form.elements[key].value=value;select.value=selected?.id||"";find("prediction-preset-updated").textContent=selected?`更新: ${stamp(selected.updated_at)}`:"";buttons();return true;};
    const refresh=async()=>{
      if(busy||refreshing)return;refreshing=true;
      try{
        const query=new URLSearchParams({sort:controls.elements.sort.value,order:controls.elements.order.value}),response=await fetch(`/api/v2/predictions?${query}`,{cache:"no-store",signal}),data=await response.json();if(!response.ok)throw Error(data.error);if(busy||!active)return;state=data;dismiss();
        if(!policyDirty){policy.elements.enabled.checked=data.policy.enabled;policy.dataset.revision=data.policy.revision;}else{policy.elements.enabled.checked=savedPolicy.enabled;policy.dataset.revision=savedPolicy.revision;}
        const id=select.value;select.replaceChildren(new Option("新しく作る",""),...data.presets.map(item=>new Option(item.name,item.id)));
        if(!dirty){select.value=data.presets.some(item=>item.id===id)?id:"";choose(select.value);}else restorePreset();
        current=data.items.find(item=>["ACTIVE","LOCKED"].includes(item.status))||data.items[0]||null;
        find("prediction-current").textContent=current?`${current.title} — ${states[current.status]}`:data.fresh?"進行中の予想はありません。":"予想の状態を確認できません。";
        find("prediction-observed").textContent=data.observed_at?`最終確認: ${new Intl.DateTimeFormat("ja-JP",{timeZone:"Asia/Tokyo",dateStyle:"short",timeStyle:"medium"}).format(new Date(data.observed_at))} JST${data.fresh?"":"（現在の状態は未確認）"}`:"認証後、記録開始中に状態を確認します。";
        const outcome=winner.value;winner.replaceChildren(new Option("正解を選ぶ",""),...(current?.outcomes||[]).map(item=>new Option(item.title,item.id)));winner.value=outcome;
        find("prediction-results").textContent=data.operations.slice(0,10).map(item=>`${names[item.action]}: ${results[item.state]||"確認中"}`).join(" ／ ")||"まだ操作はありません。";
        lock(false);
      }catch(error){if(error.name!=="AbortError")fail(error);}
      finally{refreshing=false;}
    };
    listen(form,"input",capturePreset);listen(policy,"input",capturePolicy);listen(controls,"change",refresh);listen(select,"change",()=>choose(select.value));listen(winner,"change",dismiss);
    listen(form,"submit",async event=>{event.preventDefault();if(busy)return;lock(true);try{const saved=await post("preset",{id:selected?.id||Array.from(crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,"0")).join(""),name:form.elements.name.value,revision:selected?.revision||0,specification:{title:form.elements.title.value,outcomes:form.elements.outcomes.value.split("\n").map(value=>value.trim()).filter(Boolean),prediction_window:Number(form.elements.prediction_window.value)}});savedPreset=null;dirty=false;if(active){lock(false);await refresh();select.value=saved.id;choose(saved.id);status.textContent="プリセットを保存しました。";}}catch(error){fail(error);}finally{if(active)lock(false);}});
    listen(policy,"submit",async event=>{event.preventDefault();if(busy||!state)return;lock(true);try{await post("policy",{enabled:policy.elements.enabled.checked,revision:Number(policy.dataset.revision)});savedPolicy=null;policyDirty=false;dismiss();if(active)status.textContent="新規開始の設定を保存しました。";}catch(error){fail(error);}finally{if(active){lock(false);await refresh();}}});
    const prepare=async action=>{if(busy)return;lock(true);try{preview=await post("preview",{action,target:action==="start"?selected.id:current.id,winning_outcome_id:winner.value});if(!active)return;const content=preview.content;find("prediction-preview-content").textContent=`${names[action]}: ${content.title}${action==="start"?` ／ 選択肢: ${content.outcomes.join("・")} ／ 受付: ${content.prediction_window}秒`:action==="resolve"?` ／ 正解: ${content.winning_title}`:action==="cancel"?" ／ 参加者にポイントが返還されます。":""}`;find("prediction-confirmation").hidden=false;}catch(error){fail(error);}finally{if(active)lock(false);}};
    listen(find("prediction-start"),"click",()=>prepare("start"));root.querySelectorAll("[data-action]").forEach(button=>listen(button,"click",()=>prepare(button.dataset.action)));
    listen(find("prediction-confirm"),"click",async()=>{if(busy||!preview)return;lock(true);try{await post("confirm",{id:preview.id});dismiss();if(active)status.textContent="操作を受け付けました。結果を確認しています。";}catch(error){fail(error);}finally{if(active){lock(false);await refresh();}}});
    listen(find("prediction-dismiss"),"click",dismiss);listen(find("prediction-refresh"),"click",async()=>{if(busy)return;lock(true);try{await post("refresh",{});if(active)status.textContent="Twitchの状態を再確認しています。";}catch(error){fail(error);}finally{if(active){lock(false);await refresh();}}});
    listen(document,"visibilitychange",()=>{if(document.visibilityState==="visible")refresh();});
    lock(true);refresh().then(()=>{if(active&&state)status.textContent="開催内容を確認してから操作できます。";});const interval=setInterval(()=>{if(document.visibilityState==="visible")refresh();},5000);
    return()=>{if(dirty)capturePreset();if(policyDirty)capturePolicy();preview=null;active=false;clearInterval(interval);lifecycle.abort();};
  }

  if(window.IniwaApp)window.IniwaApp.register("predictions",mount);else document.addEventListener("DOMContentLoaded",()=>mount(document),{once:true});
})();
