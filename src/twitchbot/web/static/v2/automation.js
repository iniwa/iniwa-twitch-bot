"use strict";
(()=>{
  let savedDefinition=null,savedPolicy=null,savedKind=null;
  const errors={revision_conflict:"別の変更があります。設定を選び直して確認してください。",command_name_conflict:"コマンド名または別名が他の設定と重複しています。",new_definition_must_be_disabled:"新しい設定はオフで保存してください。",automation_unavailable:"記録先と接続設定がまだ準備されていません。"};
  const labels={sent:"送信済み",failed:"失敗・確認が必要",unknown:"結果不明・再送しません",skipped:"見送り",pending:"待機中",dispatching:"送信結果を確認中"};
  const reasons={twitch_result:"Twitchの応答",queued:"送信待ち",expired:"応答の期限切れ",definition_changed:"設定が変更されました",policy_changed:"機能設定が変更されました",connection_reset:"接続または記録を停止しました",transport_failed:"接続結果を確認できません",restart_requires_review:"再起動前の結果が不明です",restore_requires_review:"復元前の結果を確認してください",rate_limited:"送信頻度の制限",authorization_scope_required:"認証権限が不足しています",shared_chat_paused:"Shared Chat中は休止します"};
  const split=value=>value.split(",").map(item=>item.trim()).filter(Boolean);
  const stamp=value=>value?`${new Intl.DateTimeFormat("ja-JP",{timeZone:"Asia/Tokyo",dateStyle:"short",timeStyle:"short"}).format(new Date(value))} JST`:"更新日時不明";
  const post=async(path,body)=>{const response=await fetch(`/api/v2/automation/${path}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}),data=await response.json();if(!response.ok)throw Error(data.error);return data;};

  function mount(root){
    const find=id=>root.querySelector(`#${id}`),form=find("automation-definition"),policy=find("automation-policy"),select=find("definition-select"),status=find("automation-status"),controls=find("definition-list-controls");
    if(!form||!policy||!select||!status||!controls)return null;
    const lifecycle=new AbortController(),signal=lifecycle.signal;
    let active=true,state=null,selected=null,busy=false,refreshing=false;
    let kind=savedKind||(location.hash==="#commands"?"command":"post");
    let dirty=Boolean(savedDefinition&&savedDefinition.kind===kind),policyDirty=Boolean(savedPolicy);
    const listen=(target,type,handler)=>target.addEventListener(type,handler,{signal});
    const fail=error=>{if(active)status.textContent=errors[error.message]||"処理を完了できませんでした。名前・本文・条件を確認してください。入力は残しています。";};
    const lock=value=>{busy=value;root.querySelectorAll("input,textarea,select,button").forEach(element=>element.disabled=value);if(!value)form.elements.enabled.disabled=!selected;};
    const specification=()=>kind==="command"?{trigger:form.elements.trigger.value,aliases:split(form.elements.aliases.value),response_type:form.elements.response_type.value,body:form.elements.body.value,role:form.elements.role.value,shared_seconds:Number(form.elements.shared_seconds.value),user_seconds:Number(form.elements.user_seconds.value)}:{body:form.elements.body.value,target:form.elements.target.value,category_id:form.elements.target.value==="category"?form.elements.category_id.value:null,minutes:Number(form.elements.minutes.value),comments:Number(form.elements.comments.value)};
    const captureDefinition=()=>{
      savedDefinition={kind,selected,values:{name:form.elements.name.value,enabled:form.elements.enabled.checked,position:form.elements.position.value,trigger:form.elements.trigger.value,aliases:form.elements.aliases.value,response_type:form.elements.response_type.value,role:form.elements.role.value,shared_seconds:form.elements.shared_seconds.value,user_seconds:form.elements.user_seconds.value,target:form.elements.target.value,category_id:form.elements.category_id.value,minutes:form.elements.minutes.value,comments:form.elements.comments.value,body:form.elements.body.value}};
      dirty=true;
    };
    const capturePolicy=()=>{savedPolicy={commands_enabled:policy.elements.commands_enabled.checked,posts_enabled:policy.elements.posts_enabled.checked,ignored:policy.elements.ignored.value,revision:policy.dataset.revision};policyDirty=true;};
    const renderKind=()=>{
      root.querySelectorAll("[data-kind]").forEach(element=>element.setAttribute("aria-pressed",String(element.dataset.kind===kind)));
      find("command-fields").hidden=kind!=="command";find("post-fields").hidden=kind!=="post";find("command-preview").hidden=kind!=="command";
      find("definition-heading").textContent=kind==="post"?"自動投稿の編集":"チャットコマンドの編集";
    };
    const renderSummary=()=>{
      find("definition-updated").textContent=selected?`更新: ${stamp(selected.updated_at)}`:"";
      find("definition-summary").textContent=selected?(selected.kind==="post"?`${selected.specification.minutes}分と${selected.specification.comments}コメントの両方を待ちます。${state?.waits.find(wait=>wait.definition_id===selected.id)?.held?"直前の結果を確認してください。本文や条件を修正して保存するまで送信を休止しています。":""}`:`全体で${selected.specification.shared_seconds}秒、同じ人は${selected.specification.user_seconds}秒待ちます。`):"新しい設定はオフで保存されます。";
    };
    const choose=id=>{
      selected=state?.definitions.find(item=>item.id===id)||null;savedDefinition=null;dirty=false;form.reset();form.elements.enabled.disabled=!selected;
      if(selected){form.elements.name.value=selected.name;form.elements.enabled.checked=selected.enabled;form.elements.position.value=selected.position;for(const [key,value] of Object.entries(selected.specification))form.elements[key].value=Array.isArray(value)?value.join(", "):value??"";}
      renderSummary();
    };
    const restoreDefinition=()=>{
      if(!savedDefinition||savedDefinition.kind!==kind)return false;
      selected=savedDefinition.selected;form.reset();for(const [key,value] of Object.entries(savedDefinition.values)){if(key==="enabled")form.elements[key].checked=value;else form.elements[key].value=value;}
      form.elements.enabled.disabled=!selected;select.value=selected?.id||"";renderSummary();return true;
    };
    const refresh=async()=>{
      if(busy||refreshing)return;refreshing=true;
      try{
        const query=new URLSearchParams({sort:controls.elements.sort.value,order:controls.elements.order.value});if(controls.elements.enabled.value)query.set("enabled",controls.elements.enabled.value);
        const response=await fetch(`/api/v2/automation?${query}`,{cache:"no-store",signal}),data=await response.json();if(!response.ok)throw Error(data.error);if(busy||!active)return;state=data;
        if(!policyDirty){policy.elements.commands_enabled.checked=data.policy.commands_enabled;policy.elements.posts_enabled.checked=data.policy.posts_enabled;policy.elements.ignored.value=data.policy.ignored.join(", ");policy.dataset.revision=data.policy.revision;}else{policy.elements.commands_enabled.checked=savedPolicy.commands_enabled;policy.elements.posts_enabled.checked=savedPolicy.posts_enabled;policy.elements.ignored.value=savedPolicy.ignored;policy.dataset.revision=savedPolicy.revision;}
        const current=select.value;select.replaceChildren(new Option("新しく作る",""),...data.definitions.filter(item=>item.kind===kind).map(item=>new Option(`${item.enabled?"オン":"オフ"} · ${item.name}`,item.id)));
        if(!dirty){select.value=data.definitions.some(item=>item.id===current)?current:"";choose(select.value);}else restoreDefinition();
        find("automation-connection").textContent=!data.sender_configured?"Botの送信用認証は未設定です。設定と内容の確認は利用できます。":data.state==="shared_chat_paused"?"Shared Chat中のため送信を休止しています。":["waiting","sent"].includes(data.state)?"接続と投稿条件を確認しています。":"送信は休止中です。記録・接続・機能の有効状態を確認してください。";
        find("dispatch-results").replaceChildren(...data.results.map(item=>{const tr=document.createElement("tr");for(const text of [data.definitions.find(definition=>definition.id===item.definition_id)?.name||"保存済みの設定",labels[item.state]||"確認中",reasons[item.reason]||"条件を確認してください"]){const td=document.createElement("td");td.textContent=text;tr.append(td);}return tr;}));
        lock(false);
      }catch(error){if(error.name!=="AbortError")fail(error);}
      finally{refreshing=false;}
    };
    listen(form,"input",captureDefinition);listen(policy,"input",capturePolicy);listen(select,"change",()=>choose(select.value));listen(controls,"change",refresh);
    root.querySelectorAll("[data-kind]").forEach(button=>listen(button,"click",()=>{if(button.dataset.kind===kind)return;if(dirty&&!window.confirm("保存していない入力を破棄して切り替えますか？"))return;savedDefinition=null;dirty=false;kind=button.dataset.kind;savedKind=kind;history.replaceState(history.state,"",kind==="command"?`${location.pathname}${location.search}#commands`:`${location.pathname}${location.search}`);renderKind();select.value="";choose("");refresh();}));
    listen(form,"submit",async event=>{event.preventDefault();if(busy)return;lock(true);try{const saved=await post("definition",{id:selected?.id||Array.from(crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,"0")).join(""),kind,name:form.elements.name.value,enabled:Boolean(selected)&&form.elements.enabled.checked,specification:specification(),revision:selected?.revision||0,position:Number(form.elements.position.value)});savedDefinition=null;dirty=false;if(active){selected=saved;status.textContent="設定を保存しました。";lock(false);await refresh();select.value=saved.id;choose(saved.id);}}catch(error){fail(error);}finally{if(active)lock(false);}});
    listen(policy,"submit",async event=>{event.preventDefault();if(busy||!state)return;lock(true);try{await post("policy",{commands_enabled:policy.elements.commands_enabled.checked,posts_enabled:policy.elements.posts_enabled.checked,ignored:split(policy.elements.ignored.value),revision:Number(policy.dataset.revision)});savedPolicy=null;policyDirty=false;if(active)status.textContent="機能設定を保存しました。";}catch(error){fail(error);}finally{if(active){lock(false);await refresh();}}});
    listen(find("preview-command"),"click",async()=>{if(busy)return;lock(true);try{const value=await post("preview",{specification:specification(),input:find("preview-input").value,role:find("preview-role").value});if(active)find("preview-result").textContent=value.response||"この入力・役割では応答しません。";}catch(error){fail(error);}finally{if(active)lock(false);}});
    listen(document,"visibilitychange",()=>{if(document.visibilityState==="visible")refresh();});
    renderKind();lock(true);refresh().then(()=>{if(active&&state)status.textContent="設定を保存してから、使う機能を有効にしてください。";});
    const interval=setInterval(()=>{if(document.visibilityState==="visible")refresh();},10000);
    return()=>{if(dirty)captureDefinition();if(policyDirty)capturePolicy();active=false;clearInterval(interval);lifecycle.abort();};
  }

  if(window.IniwaApp)window.IniwaApp.register("automation",mount);else document.addEventListener("DOMContentLoaded",()=>mount(document),{once:true});
})();
