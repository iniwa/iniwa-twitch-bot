"use strict";
(()=>{
  const mount=root=>{
    const lifecycle=new AbortController(),signal=lifecycle.signal;
    root.querySelectorAll("time[datetime]").forEach(el=>{
      el.textContent=new Intl.DateTimeFormat("ja-JP",{timeZone:"Asia/Tokyo",dateStyle:"medium",timeStyle:"medium"}).format(new Date(el.dateTime))+" JST";
    });
    const deletionForm=root.querySelector("#deletion-form");
    if(!deletionForm)return()=>lifecycle.abort();
    let preview=null;
    const result=root.querySelector("#deletion-result"),confirm=root.querySelector("#delete-confirm");
    const post=async(url,body)=>{const response=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw new Error(data.error||"request_failed");return data;};
    deletionForm.addEventListener("input",()=>{preview=null;confirm.hidden=true;result.textContent="期間が変更されました。削除対象を再確認してください。";},{signal});
    deletionForm.addEventListener("submit",async event=>{
      event.preventDefault();confirm.hidden=true;preview=null;
      const start=root.querySelector("#delete-start").value,end=root.querySelector("#delete-end").value,button=deletionForm.querySelector("button");button.disabled=true;
      try{
        const data=await post("/api/v2/chat-body-deletion-previews",{start:new Date(start+"+09:00").toISOString(),end:new Date(end+"+09:00").toISOString()});
        if(signal.aborted||start!==root.querySelector("#delete-start").value||end!==root.querySelector("#delete-end").value)return;
        preview=data;result.textContent=`${start.replace("T"," ")} ～ ${end.replace("T"," ")}（JST・終了時刻を含まない）の本文 ${data.message_count}件が対象です。`;confirm.hidden=data.message_count===0;
      }catch(error){if(!signal.aborted)result.textContent="対象を確認できませんでした。期間と保存先の状態を確認してください。";}finally{if(!signal.aborted)button.disabled=false;}
    },{signal});
    confirm.addEventListener("click",async()=>{
      if(!preview)return;confirm.disabled=true;
      try{
        const data=await post("/api/v2/chat-body-deletions",{preview_id:preview.id});if(signal.aborted)return;
        result.textContent=`本文 ${data.message_count}件を削除しました。発言数と履歴は残っています。`;confirm.hidden=true;preview=null;
        root.querySelectorAll(".chat-body").forEach(el=>el.textContent="表示を更新してください。");
        const reload=document.createElement("button");reload.textContent="履歴を更新";reload.addEventListener("click",()=>location.reload(),{signal});result.append(reload);
      }catch(error){if(!signal.aborted)result.textContent=error.message==="preview_changed"||error.message==="preview_expired"?"確認後に対象が変わったか期限が切れました。削除対象を再確認してください。":"削除結果を確認できませんでした。再度押すと同じ操作の結果を確認します。";}finally{if(!signal.aborted)confirm.disabled=false;}
    },{signal});
    return()=>lifecycle.abort();
  };
  if(window.IniwaApp)window.IniwaApp.register("community",mount);else mount(document);
})();
