// Synthetic fixture only: exercise actual worker lifecycle and mounted NAS.
const {chromium}=require('playwright');
(async()=>{
 const url=process.env.QA_URL;
 if(!url || !/^http:\/\/127\.0\.0\.1:\d+$/.test(url))throw Error('Explicit loopback QA_URL required');
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const check=(ok,message)=>{if(!ok)throw Error(message);};
 const status=()=>page.request.get(url+'/api/v2/operations').then(r=>r.json());
 const until=async predicate=>{for(let i=0;i<80;i++){const data=await status();if(predicate(data))return data;await page.waitForTimeout(500);}throw Error('Operation did not finish');};
 try{
  const fixture=await page.request.get(url+'/qa/state').then(r=>r.json());
  check(fixture.synthetic===true && fixture.calls===0,'fixture not fresh/inert');
  await page.goto(url+'/v2/settings');
  await page.locator('#recording-toggle').filter({hasText:'記録を開始'}).click();
  await until(data=>data.connection.state==='ready');
  await page.reload();await page.locator('#manual-backup').click();
  let data=await until(data=>data.backups.items.some(item=>item.state==='nas_verified'));
  check(data.jobs.length===1 && data.jobs[0].state==='succeeded','manual job failed');
  await page.reload();await page.locator('#restore-backup').selectOption(data.backups.items[0].id);
  await page.locator('#restore-candidate-form button').click();
  await until(data=>data.restore_jobs.some(job=>job.state==='verified'));
  await page.reload();await page.locator('#backup-policy input').check();
  await page.locator('#backup-policy select').selectOption('4');
  await page.locator('#backup-policy button').click();
  data=await until(data=>data.backups.items.some(item=>item.reasons.includes('daily') && item.state==='nas_verified'));
  await page.request.post(url+'/qa/stream',{data:{live:false}});
  data=await until(data=>data.backups.items.some(item=>item.stream_ids.includes('s1') && item.state==='nas_verified'));
  await page.reload();await page.locator('#recording-toggle').filter({hasText:'記録を停止'}).click();
  await until(data=>!data.enabled);
  const stopped=await page.request.get(url+'/api/v2/live').then(r=>r.json());
  check(stopped.stream.id===null,'stopped stream leaked');
  for(const width of [1440,1024,736,360]){
   await page.setViewportSize({width,height:1000});await page.reload();
   check(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),`overflow ${width}`);
   if(process.env.QA_ARTIFACT_DIR && [1440,360].includes(width))await page.screenshot({path:`${process.env.QA_ARTIFACT_DIR}/settings-${width}.png`,fullPage:true});
  }
  const final=await page.request.get(url+'/qa/state').then(r=>r.json());
  const synthetic=await status();
  const sample=synthetic.backups.items[0];
  synthetic.backups.items.unshift({...sample,id:'expired-fixture',state:'expired'},{...sample,id:'retiring-fixture',state:'retiring'});
  await page.route('**/api/v2/operations',route=>route.fulfill({json:synthetic}));
  await page.reload();await page.locator('#backup-items').getByText('保存期限により整理済み').waitFor();
  check(await page.locator('#restore-backup option[value="expired-fixture"],#restore-backup option[value="retiring-fixture"]').count()===0,'retired copies offered for restore');
  await page.unroute('**/api/v2/operations');
  check(final.request_thread_calls.length===0,'HTTP request called Twitch');
  check(errors.length===0,errors.join(';'));
  console.log(JSON.stringify({manualBackup:'nas_verified',dailyBackup:'nas_verified',streamEndBackup:'nas_verified',restoreCandidate:'verified_inactive',retiredRestoreChoices:'excluded',stopped:'passed',layouts:4,pageErrors:errors,nasFixture:final.nas_fixture}));
 }finally{await browser.close();}
})();
