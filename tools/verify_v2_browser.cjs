// Explicit synthetic device QA only. Requires Playwright and Edge.
const {chromium}=require('playwright');
const fs=require('fs');
(async()=>{
 const url=process.env.QA_URL;
 if(!url || !/^http:\/\/127\.0\.0\.1:\d+$/.test(url))throw Error('QA_URL must be an explicit loopback URL');
 const artifacts=process.env.QA_ARTIFACT_DIR;
 if(!artifacts || !fs.statSync(artifacts).isDirectory())throw Error('QA_ARTIFACT_DIR must exist');
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 await page.addInitScript(()=>{Date.now=()=>new Date().getTime()+(sessionStorage.getItem('qa-clock-offset')==='ahead' ? 3600000 : -3600000);});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const check=(ok,message)=>{if(!ok)throw Error(message);};
 const state=()=>page.request.get(url+'/qa/state').then(r=>r.json());
 try{
  check((await state()).synthetic===true,'refusing non-synthetic server');
  await page.goto(url+'/v2/presets');
  await page.locator('#preset-select').selectOption('p1');
  await page.locator('input[name=title]').fill('確認用の新しいタイトル');
  check(await page.locator('#preview-preset').isDisabled(),'dirty preset could apply');
  await page.reload();await page.locator('#preset-select').selectOption('p1');
  check(await page.locator('input[name=title]').inputValue()==='確認用の新しいタイトル','draft lost');
  await page.locator('#preset-form button').click();
  await page.locator('#preset-status').filter({hasText:'セットを保存しました'}).waitFor();
  check((await state()).calls.length===0,'saving preset sent externally');
  await page.locator('#preview-preset').click();await page.locator('#preset-preview').waitFor({state:'visible'});
  check((await state()).calls.length===0,'preview sent externally');
  await page.locator('#apply-preset').click();await page.locator('#apply-result').filter({hasText:'完了'}).waitFor();
  await page.locator('#apply-preset').click();
  check((await state()).calls.length===1,'double application sent');
  await page.goto(url+'/v2/community/people/u0');
  await page.locator('#person-note-form textarea').fill('次回も覚えておきたいこと');
  await page.goto(url+'/v2/community/people/u1');await page.goto(url+'/v2/community/people/u0');
  await page.locator('#person-note-form textarea').waitFor();
  await page.waitForFunction(()=>!document.querySelector('#person-note-form textarea').disabled);
  check(await page.locator('#person-note-form textarea').inputValue()==='次回も覚えておきたいこと','person draft lost');
  await page.locator('#person-note-form button').click();await page.locator('#person-note-result').filter({hasText:'保存しました'}).waitFor();
  await page.goto(url+'/v2/control');
  check(await page.locator('#current-viewers').innerText()==='34','PC clock behind server hid current viewers');
  await page.locator('#stream-note').fill('ここが見どころ');await page.locator('#stream-note-form button').click();await page.locator('#note-result').filter({hasText:'保存しました'}).waitFor();
  check((await state()).calls.length===1,'local note sent marker');
  await page.locator('#stream-note').fill('Twitchにも印を付ける');await page.locator('#request-marker').check();await page.locator('#stream-note-form button').click();
  await page.locator('#note-result').filter({hasText:'完了'}).waitFor();
  check((await state()).calls.length===2,'marker did not dispatch once');
  await page.goto(url+'/v2/community/chat');await page.locator('.deletion summary').click();
  await page.locator('#delete-start').fill('2020-01-01T00:00');await page.locator('#delete-end').fill('2030-01-01T00:00');
  await page.locator('#deletion-form button').click();await page.locator('#delete-confirm').waitFor({state:'visible'});
  await page.locator('#delete-confirm').click();await page.locator('#deletion-result').filter({hasText:'本文 4件を削除しました'}).waitFor();
  check((await state()).comments===1,'body deletion changed participation');
  const layouts=[];
  for(const width of [1440,1024,736,360]){
   await page.setViewportSize({width,height:1000});
   for(const path of ['/v2/control','/v2/community','/v2/community/events','/v2/community/followers','/v2/community/people/u0','/v2/community/chat','/v2/presets','/v2/history/s1']){
    await page.goto(url+path);await page.waitForTimeout(50);
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
    check(!overflow,`overflow ${width} ${path}`);check(!errors.length,errors.join(';'));
    layouts.push({width,path});
    if((width===1440 && ['/v2/control','/v2/community/followers'].includes(path)) || (width===360 && path==='/v2/control'))await page.screenshot({path:`${artifacts}/integrated-${path.split('/').pop()}-${width}.png`,fullPage:true});
   }
  }
  await page.emulateMedia({colorScheme:'dark'});await page.setViewportSize({width:1440,height:1000});await page.goto(url+'/v2/control');await page.screenshot({path:artifacts+'/integrated-control-dark.png',fullPage:true});
  await page.evaluate(()=>sessionStorage.setItem('qa-clock-offset','ahead'));
  await page.reload();
  check(await page.locator('#current-viewers').innerText()==='34','PC clock ahead of server hid current viewers');
  await page.route('**/api/v2/control',route=>route.abort());
  await page.evaluate(()=>{const original=performance.now.bind(performance);performance.now=()=>original()+61000;});
  await page.waitForFunction(()=>document.getElementById('current-viewers').textContent==='—');
  console.log(JSON.stringify({interactions:'passed',layouts:layouts.length,clockSkew:'passed',staleWithoutPolling:'passed',pageErrors:errors}));
 }finally{await browser.close();}
})();
