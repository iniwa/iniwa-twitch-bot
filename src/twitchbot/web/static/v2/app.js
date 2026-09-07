"use strict";
(()=>{
  const modules=new Map(),cache=new Map(),states=new Map();
  const MAX_PAGES=5,MAX_BYTES=2*1024*1024,FRESH_MS=60_000;
  const STYLE_FILES=new Set(["app.css","community.css","control.css","automation.css"]);
  const scriptUrl=document.currentScript?.src;
  const staticBase=scriptUrl?new URL("./",scriptUrl):null;
  let activeCleanup=null,activeController=null,generation=0,ready=false,displayedUrl=location.href;

  const pageFamily=root=>(root?.dataset.page||"").split("-")[0];
  const supported=url=>{
    if(url.origin!==location.origin)return false;
    if(/^\/v2\/history(?:\/|$)/.test(url.pathname))return true;
    return ["/v2/control","/v2/presets","/v2/community","/v2/community/events","/v2/community/followers","/v2/community/followers/current","/v2/settings","/v2/automation","/v2/predictions"].includes(url.pathname);
  };
  const normalized=url=>`${url.pathname}${url.search}`;
  const currentMain=()=>document.querySelector("main[data-page]");
  const currentSupported=()=>supported(new URL(location.href));
  const cacheSize=()=>[...cache.values()].reduce((total,item)=>total+item.html.length*2,0);
  const cachePut=(key,item)=>{
    cache.delete(key);cache.set(key,item);
    while(cache.size>MAX_PAGES||cacheSize()>MAX_BYTES)cache.delete(cache.keys().next().value);
  };
  const remember=()=>{
    const focused=document.activeElement;
    states.set(normalized(new URL(displayedUrl)),{scrollY,focusId:focused?.id||null});
  };
  const stopPage=()=>{
    if(activeCleanup){try{activeCleanup();}finally{activeCleanup=null;}}
  };
  const mount=root=>{
    stopPage();
    const factory=modules.get(pageFamily(root));
    activeCleanup=factory?.(root,{navigate})||null;
  };
  const ensureModule=family=>{
    if(modules.has(family))return Promise.resolve();
    const files={history:["history.js"],community:["community.js"],settings:["settings.js"],automation:["automation.js"],predictions:["predictions.js"],live:["history.js","control.js"],presets:["history.js","control.js"]}[family];
    if(!files||!scriptUrl)return Promise.reject(Error("unsupported_page_module"));
    const loaded=file=>file==="history.js"?modules.has("history"):file==="control.js"?modules.has("live")&&modules.has("presets"):modules.has(file.slice(0,-3));
    const load=file=>loaded(file)?Promise.resolve():new Promise((resolve,reject)=>{const script=document.createElement("script");script.src=new URL(`./${file}`,scriptUrl).href;script.onload=resolve;script.onerror=()=>reject(Error("page_module_failed"));document.head.append(script);});
    return files.reduce((promise,file)=>promise.then(()=>load(file)),Promise.resolve()).then(()=>{if(!modules.has(family))throw Error("page_module_missing");});
  };
  const ensureStyles=doc=>{
    const loaded=new Set([...document.querySelectorAll('link[rel="stylesheet"]')].map(link=>link.href));
    return Promise.all([...doc.querySelectorAll('link[rel="stylesheet"]')]
      .map(link=>new URL(link.getAttribute("href"),location.href).href)
      .filter(href=>{const url=new URL(href),file=url.pathname.split("/").pop();return staticBase&&STYLE_FILES.has(file)&&url.href===new URL(file,staticBase).href&&!loaded.has(href);})
      .map(href=>new Promise((resolve,reject)=>{
        const link=document.createElement("link");link.rel="stylesheet";link.href=href;
        link.onload=resolve;link.onerror=()=>reject(Error("stylesheet_failed"));document.head.append(link);
      })));
  };
  const parse=(html,responseUrl)=>{
    const doc=new DOMParser().parseFromString(html,"text/html"),root=doc.querySelector("main[data-page]");
    const url=new URL(responseUrl,location.href);
    if(!root||!supported(url)||url.origin!==location.origin)throw Error("invalid_shell");
    return {doc,root,url,title:doc.title};
  };
  const updateNav=family=>{
    const key=family==="history"?"/v2/history":family==="live"?"/v2/control":family==="presets"?"/v2/presets":family==="settings"?"/v2/settings":["automation","predictions"].includes(family)?"/v2/automation":"/v2/community";
    document.querySelectorAll(".app-nav-link").forEach(link=>{
      const selected=new URL(link.href).pathname===key;
      if(selected)link.setAttribute("aria-current","page");else link.removeAttribute("aria-current");
    });
  };
  const showRefreshNotice=(text="新しい記録があります。 ")=>{
    const root=currentMain();if(!root||root.querySelector(".app-refresh-notice"))return;
    const notice=document.createElement("p");notice.className="app-refresh-notice";notice.setAttribute("role","status");
    notice.append(text);
    const link=document.createElement("a");link.href=location.href;link.textContent="更新";notice.append(link);root.prepend(notice);
  };
  const apply=async(parsed,{restore=false}={})=>{
    await Promise.all([ensureStyles(parsed.doc),ensureModule(pageFamily(parsed.root))]);
    const imported=document.importNode(parsed.root,true),old=currentMain();
    if(!old)throw Error("missing_current_main");
    stopPage();old.replaceWith(imported);document.title=parsed.title;updateNav(pageFamily(imported));mount(imported);
    displayedUrl=parsed.url.href;
    if(normalized(new URL(location.href))!==normalized(parsed.url))history.replaceState({iniwa:true},"",parsed.url);
    const saved=states.get(normalized(parsed.url));
    if(restore&&saved){scrollTo(0,saved.scrollY);if(saved.focusId)document.getElementById(saved.focusId)?.focus({preventScroll:true});}
    else{scrollTo(0,0);const heading=imported.querySelector("h1");if(heading){heading.tabIndex=-1;heading.focus({preventScroll:true});}}
  };
  const fetchPage=async(url,token)=>{
    activeController?.abort();activeController=new AbortController();
    const response=await fetch(url,{headers:{"X-Iniwa-Partial":"1"},cache:"no-store",signal:activeController.signal,redirect:"follow"});
    if(!response.ok||!response.headers.get("content-type")?.includes("text/html"))throw Error("invalid_response");
    const html=await response.text();if(token!==generation)throw Error("stale_navigation");
    const parsed=parse(html,response.url);cachePut(normalized(parsed.url),{html,url:parsed.url.href,fetchedAt:Date.now()});return parsed;
  };
  const revalidate=async(url,shownHtml,token)=>{
    try{
      const parsed=await fetchPage(url,token),fresh=cache.get(normalized(parsed.url));
      if(token===generation&&fresh?.html!==shownHtml)showRefreshNotice();
    }catch(error){if(error.name!=="AbortError"&&error.message!=="stale_navigation")showRefreshNotice("最新の状態を確認できませんでした。 ");}
  };
  async function navigate(value,{mode="push",restore=false}={}){
    const url=new URL(value,location.href);if(!supported(url)||!currentSupported()){location.assign(url.href);return;}
    const token=++generation,key=normalized(url),cached=cache.get(key),fresh=cached&&Date.now()-cached.fetchedAt<=FRESH_MS;
    remember();const oldMain=currentMain(),oldTitle=document.title,oldUrl=displayedUrl;
    if(mode==="push")historyPush(url);else if(mode==="replace")history.replaceState({iniwa:true},"",url);
    try{
      if(fresh){const parsed=parse(cached.html,cached.url);await apply(parsed,{restore});revalidate(url,cached.html,token);return;}
      const placeholder=document.createElement("main"),family=url.pathname.startsWith("/v2/history")?"history":url.pathname==="/v2/control"?"live":url.pathname==="/v2/presets"?"presets":url.pathname==="/v2/settings"?"settings":url.pathname==="/v2/automation"?"automation":url.pathname==="/v2/predictions"?"predictions":"community";placeholder.dataset.page=`${family}-loading`;placeholder.setAttribute("aria-busy","true");
      const heading=document.createElement("h1");heading.textContent=family==="history"?"配信履歴":family==="live"?"ライブ":family==="presets"?"配信セット":family==="settings"?"設定とバックアップ":family==="automation"?"自動化":family==="predictions"?"予想":"コミュニティ";
      const status=document.createElement("p");status.setAttribute("role","status");status.textContent="読み込んでいます。";placeholder.append(heading,status);
      stopPage();oldMain.replaceWith(placeholder);
      const parsed=await fetchPage(url,token);await apply(parsed,{restore});
    }catch(error){
      if(error.name==="AbortError"||error.message==="stale_navigation")return;
      if(["invalid_response","invalid_shell","unsupported_page_module","page_module_missing","page_module_failed","stylesheet_failed"].includes(error.message)){location.assign(url.href);return;}
      const now=currentMain();if(now&&oldMain&&!oldMain.isConnected){stopPage();now.replaceWith(oldMain);document.title=oldTitle;mount(oldMain);}
      history.replaceState({iniwa:true},"",oldUrl);displayedUrl=oldUrl;
      const message=document.createElement("p");message.className="app-navigation-error";message.setAttribute("role","alert");message.textContent="画面を切り替えられませんでした。通信状態を確認して、もう一度お試しください。";oldMain?.prepend(message);
    }
  }
  const historyPush=url=>history.pushState({iniwa:true},"",url);
  const eligibleLink=event=>{
    const link=event.target.closest("a[href]");
    if(!link||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||link.target||link.download||link.relList.contains("external")||link.origin!==location.origin)return null;
    const url=new URL(link.href),current=new URL(location.href);
    if(url.pathname===current.pathname&&url.search===current.search&&url.hash)return null;
    return supported(url)&&currentSupported()?url:null;
  };

  window.IniwaApp={register(name,factory){modules.set(name,factory);if(ready&&pageFamily(currentMain())===name)mount(currentMain());}};
  document.addEventListener("DOMContentLoaded",()=>{
    ready=true;history.replaceState({iniwa:true},"",location.href);mount(currentMain());
    if(currentSupported())cachePut(normalized(new URL(displayedUrl)),{html:`<!doctype html>${document.documentElement.outerHTML}`,url:displayedUrl,fetchedAt:Date.now()});
  });
  document.addEventListener("click",event=>{const url=eligibleLink(event);if(!url)return;event.preventDefault();navigate(url);});
  document.addEventListener("submit",event=>{
    const form=event.target;if(event.defaultPrevented||form.method.toLowerCase()!=="get"||!currentSupported())return;
    const url=new URL(form.action||location.href);if(!supported(url))return;
    event.preventDefault();url.search=new URLSearchParams(new FormData(form)).toString();navigate(url);
  });
  addEventListener("popstate",()=>navigate(location.href,{mode:"pop",restore:true}));
  addEventListener("pagehide",()=>{remember();activeController?.abort();stopPage();});
})();
