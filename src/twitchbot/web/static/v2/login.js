"use strict";
(()=>{
 const status=document.getElementById("login-status"),cards=[...document.querySelectorAll("[data-login-role]")];
 const labels={not_connected:"未接続です。",starting:"ログインの準備をしています。",awaiting_login:"Twitchでのログインを待っています。",validating:"アカウントと権限を確認しています。",connected:"認証を確認し、保存しました。",saved:"保存済みの認証を使用します。",refreshing:"認証を更新しています。",stopped:"アプリは停止中です。",cancelled:"ログインを取り消しました。",oauth_wrong_account:"設定されたアカウントと違います。Twitchでアカウントを切り替えて、もう一度ログインしてください。",oauth_scope_required:"必要な権限が許可されていません。もう一度ログインしてください。",oauth_code_expired:"確認コードの期限が切れました。もう一度開始してください。",authorization_required:"再ログインが必要です。",oauth_validation_unavailable:"Twitchとの接続確認を再試行します。",oauth_unavailable:"認証の結果を確認できません。もう一度ログインしてください。",oauth_save_failed:"認証を保存できません。保存先を確認してください。",oauth_runtime_unavailable:"アプリの起動が完了していません。",oauth_not_configured:"初回の接続設定が必要です。"};
 let busy=false,refreshing=false,actionError="";
 const refresh=async()=>{
  if(busy||refreshing)return;refreshing=true;
  try{
   const response=await fetch("/api/v2/login",{cache:"no-store"}),data=await response.json();if(!response.ok)throw Error(data.error);if(busy)return;
   status.textContent=actionError||(!data.configured?"初回のアプリ登録情報と、認証の保存先を設定するとログインできます。":!data.ready?"アプリの起動を待っています。":"配信者とBotは、それぞれのアカウントで接続してください。");
   for(const card of cards){
    const item=data.accounts[card.dataset.loginRole],state=item?.state;
    card.querySelector(".login-state").textContent=item?(labels[state]||"接続できませんでした。もう一度ログインしてください。"):"このアカウントの接続設定はまだありません。";
    card.querySelector(".login-start").disabled=!data.ready||!item||["starting","awaiting_login","validating","refreshing"].includes(state);
    card.querySelector(".login-cancel").hidden=!["starting","awaiting_login"].includes(state);
    const instructions=card.querySelector(".login-instructions");instructions.hidden=state!=="awaiting_login";
    const link=card.querySelector(".login-link");link.removeAttribute("href");card.querySelector(".login-code").textContent="";
    if(state==="awaiting_login"){
     const uri=new URL(item.verification_uri);if(uri.origin!=="https://www.twitch.tv"||uri.pathname!=="/activate")throw Error("invalid_login_url");
     link.href=uri.href;card.querySelector(".login-code").textContent=item.user_code;
    }
   }
  }catch(error){status.textContent=labels[error.message]||"接続状態を確認できませんでした。";}finally{refreshing=false;}
 };
 const send=async(card,action)=>{
  if(busy)return;busy=true;actionError="";cards.forEach(c=>c.querySelectorAll("button").forEach(b=>b.disabled=true));
  try{
   const response=await fetch("/api/v2/login/"+action,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({role:card.dataset.loginRole,predictions:card.querySelector(".login-predictions")?.checked||false})});
   const result=await response.json();if(!response.ok)throw Error(result.error);
  }catch(error){actionError=labels[error.message]||"ログインを開始できませんでした。";status.textContent=actionError;}finally{busy=false;cards.forEach(c=>c.querySelector(".login-cancel").disabled=false);await refresh();}
 };
 cards.forEach(card=>{card.querySelector(".login-start").addEventListener("click",()=>send(card,"start"));card.querySelector(".login-cancel").addEventListener("click",()=>send(card,"cancel"));});
 refresh();setInterval(refresh,1000);
})();
