"use strict";
(()=>{
  const mount=root=>{
    const lifecycle=new AbortController(),signal=lifecycle.signal;
    root.querySelectorAll("time[datetime]").forEach(el=>{
      el.textContent=new Intl.DateTimeFormat("ja-JP",{timeZone:"Asia/Tokyo",dateStyle:"medium",timeStyle:"short"}).format(new Date(el.dateTime))+" JST";
    });
    const form=root.querySelector("#compare-form");
    form?.addEventListener("submit",event=>{
      if(form.querySelectorAll("input:checked").length!==2){
        event.preventDefault();root.querySelector("#selection-hint").textContent="比較する配信を2件選択してください。";form.querySelector("input")?.focus();
      }
    },{signal});
    const payload=root.querySelector("#history-data");
    if(payload){
      const items=JSON.parse(payload.textContent),ns="http://www.w3.org/2000/svg";
      const maxCount=items.reduce((largest,item)=>item.segments.reduce((n,s)=>Math.max(n,s.max),Math.max(largest,item.max_viewers||0)),1);
      const maxDuration=Math.max(1,...items.map(item=>(Date.parse(item.range_end)-Date.parse(item.range_start))/1000));
      const element=(name,attrs,content)=>{const el=document.createElementNS(ns,name);Object.entries(attrs).forEach(([key,value])=>el.setAttribute(key,value));if(content!==undefined)el.textContent=content;return el;};
      root.querySelectorAll(".viewer-chart").forEach(container=>{
        const item=items[Number(container.dataset.index)];
        const draw=()=>{
          const width=Math.max(280,container.clientWidth),right=width-20,span=width-68;
          const svg=element("svg",{viewBox:`0 0 ${width} 240`,role:"img","aria-label":"同接推移。線がない区間は欠測です。"});
          const x=at=>48+(Date.parse(at)-Date.parse(item.range_start))/1000/maxDuration*span;
          const y=value=>200-value/maxCount*174,offset=(Date.parse(item.range_start)-Date.parse(item.started_at))/1000;
          [0,.5,1].forEach(f=>{svg.append(element("line",{x1:48,y1:y(f*maxCount),x2:right,y2:y(f*maxCount),class:"grid"}));svg.append(element("text",{x:40,y:y(f*maxCount)+4,"text-anchor":"end"},String(Math.round(f*maxCount))));svg.append(element("text",{x:48+span*f,y:226,"text-anchor":f===0?"start":f===1?"end":"middle"},`${Math.round((offset+maxDuration*f)/60)}分`));});
          svg.append(element("path",{class:"series",d:item.segments.map((s,i)=>{
            if(item.graph.method==="raw")return(i>0&&item.segments[i-1].end===s.start?`V${y(s.first)}`:`M${x(s.start)},${y(s.first)}`)+`H${x(s.end)}`;
            return s.min===s.max?`M${x(s.start)},${y(s.min)}H${x(s.end)}`:`M${x(s.start)},${y(s.min)}V${y(s.max)}H${x(s.end)}V${y(s.min)}Z`;
          }).join(" ")}));
          container.replaceChildren(svg);
          if(!item.segments.length||item.graph.method!=="raw"){
            const note=document.createElement("p");note.textContent=item.graph.method==="range_required"?"欠測区間が多いため、期間を絞ってグラフを表示してください。集計値は指定期間全体の値です。":item.graph.method==="min_max_first_last"?"長時間の記録を区間ごとの最小〜最大の幅で表示しています。":"新方式の同接記録はありません。";container.append(note);
          }
        };
        draw();window.addEventListener("resize",draw,{signal});
      });
      root.querySelectorAll("[data-range]").forEach(input=>{const value=items[0][`range_${input.dataset.range}`];input.value=new Date(Date.parse(value)+9*60*60*1000).toISOString().slice(0,19);});
      root.querySelector("#range-form")?.addEventListener("submit",event=>{
        event.currentTarget.querySelectorAll("[data-range]").forEach(input=>{event.currentTarget.elements[input.dataset.range].value=new Date(`${input.value}+09:00`).toISOString();});
      },{signal});
    }
    return()=>lifecycle.abort();
  };
  window.IniwaHistoryMount=mount;
  if(window.IniwaApp)window.IniwaApp.register("history",mount);else mount(document);
})();
