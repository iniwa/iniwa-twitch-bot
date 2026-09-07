// Disposable operational fixture only; all Twitch replies and chats are synthetic.
const {chromium}=require('playwright');
(async()=>{
 const url=process.env.QA_URL;
 if(!url||!/^http:\/\/127\.0\.0\.1:\d+$/.test(url))throw Error('Explicit loopback QA_URL required');
 const browser=await chromium.launch({headless:true,channel:'msedge'}),page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const get=path=>page.request.get(url+path).then(r=>r.json());
 const check=(ok,text)=>{if(!ok)throw Error(text);};
 const until=async(path,predicate)=>{for(let n=0;n<100;n++){const d=await get(path);if(predicate(d))return d;await page.waitForTimeout(400);}throw Error('Timed out: '+path);};
 try{
  const fixture=await get('/qa/state');check(fixture.synthetic&&fixture.calls===0,'fixture must be fresh/inert');
  await page.goto(url+'/v2/automation');await until('/api/v2/automation',d=>d.policy?.revision===0);
  await page.locator('[data-kind=command]').click();
  const form=page.locator('#automation-definition');
  await form.locator('[name=name]').fill('案内コマンド');await form.locator('[name=trigger]').fill('!hello');await form.locator('[name=aliases]').fill('!hi');await form.locator('[name=body]').fill('こんにちは、配信を楽しんでください。');
  await form.locator('button').click();await until('/api/v2/automation',d=>d.definitions.length===1);
  await page.locator('#preview-input').fill('!hi');await page.locator('#preview-command').click();await page.locator('#preview-result').filter({hasText:'こんにちは'}).waitFor();
  check((await get('/qa/state')).sent.length===0,'preview posted a chat');
  await form.locator('[name=enabled]').check();await form.locator('button').click();await until('/api/v2/automation',d=>d.definitions[0].enabled);
  await page.locator('#automation-policy [name=commands_enabled]').check();await page.locator('#automation-policy button').click();await until('/api/v2/automation',d=>d.policy.commands_enabled);
  await page.goto(url+'/v2/settings');await page.locator('#recording-toggle').filter({hasText:'記録を開始'}).click();
  await until('/api/v2/operations',d=>d.connection.state==='ready'&&d.workers.events?.result?.state==='connected');
  await page.request.post(url+'/qa/chat',{data:{text:'!hi'}});await until('/qa/state',d=>d.sent.length===1);
  await page.goto(url+'/v2/predictions');
  const preset=page.locator('#prediction-preset');
  await preset.locator('[name=name]').fill('次の挑戦');await preset.locator('[name=title]').fill('クリアできる？');await preset.locator('[name=outcomes]').fill('はい\nいいえ');await preset.locator('button').click();
  await until('/api/v2/predictions',d=>d.presets.length===1);
  await page.locator('#prediction-policy input').check();await page.locator('#prediction-policy button').click();await until('/api/v2/predictions',d=>d.policy.enabled&&d.fresh);
  await page.locator('#prediction-start').click();await page.locator('#prediction-preview-content').filter({hasText:'クリアできる？'}).waitFor();
  check((await get('/api/v2/predictions')).items.length===0,'preview started prediction');
  await page.locator('#prediction-confirm').click();await until('/api/v2/predictions',d=>d.items[0]?.status==='ACTIVE');
  await page.reload();await page.locator('[data-action=lock]').click();await page.locator('#prediction-confirm').click();await until('/api/v2/predictions',d=>d.items[0]?.status==='LOCKED');
  await page.reload();await page.locator('#prediction-policy input').uncheck();await page.locator('#prediction-policy button').click();await until('/api/v2/predictions',d=>!d.policy.enabled);
  await page.request.post(url+'/qa/stream',{data:{live:false}});await until('/api/v2/live',d=>d.stream.state==='offline');
  await page.reload();await page.locator('#prediction-winner').selectOption('0');await page.locator('[data-action=resolve]').click();await page.locator('#prediction-preview-content').filter({hasText:'正解: はい'}).waitFor();await page.locator('#prediction-confirm').click();await until('/api/v2/predictions',d=>d.items[0]?.status==='RESOLVED');
  for(const path of ['/v2/automation','/v2/predictions'])for(const width of [1440,1024,736,360]){
   await page.setViewportSize({width,height:1000});await page.goto(url+path);await page.waitForTimeout(100);
   check(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),`overflow ${path} ${width}`);
   if(process.env.QA_ARTIFACT_DIR&&[1440,360].includes(width))await page.screenshot({path:`${process.env.QA_ARTIFACT_DIR}/${path.split('/').pop()}-${width}.png`,fullPage:true});
  }
  const final=await get('/qa/state');check(final.request_thread_calls.length===0,'HTTP thread called Twitch');check(errors.length===0,errors.join(';'));
  console.log(JSON.stringify({command:'eventsub_to_worker_sent',preview:'no_external_write',prediction:'start_lock_offline_resolve',layouts:8,pageErrors:errors}));
 }finally{await browser.close();}
})();
