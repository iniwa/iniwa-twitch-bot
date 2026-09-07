// Synthetic OAuth only; never navigate to or authorize a real Twitch account.
const {chromium}=require('playwright');
(async()=>{
 const url=process.env.QA_URL;
 if(!url||!/^http:\/\/127\.0\.0\.1:\d+$/.test(url))throw Error('Explicit loopback QA_URL required');
 const browser=await chromium.launch({headless:true,channel:'msedge'}),page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const check=(ok,message)=>{if(!ok)throw Error(message);};
 const get=path=>page.request.get(url+path).then(r=>r.json());
 const until=async predicate=>{for(let i=0;i<80;i++){const state=await get('/api/v2/login');if(predicate(state))return state;await page.waitForTimeout(500);}throw Error('Login did not finish');};
 try{
  check((await get('/qa/state')).synthetic,'not synthetic');
  await page.goto(url+'/v2/connect');
  for(const role of ['broadcaster','bot']){
   const card=page.locator(`[data-login-role="${role}"]`);
   await card.locator('.login-start').click();
   await until(s=>s.accounts[role].state==='awaiting_login');
   await card.locator('.login-link').waitFor({state:'visible'});
   check((await card.locator('.login-link').getAttribute('href')).startsWith('https://www.twitch.tv/activate'),'unexpected authorization destination');
   await page.request.post(url+'/qa/login/approve',{data:{role}});
   await until(s=>s.accounts[role].state==='connected');
  }
  check(!(await get('/api/v2/operations')).enabled,'login enabled recording');
  const exposed=JSON.stringify(await get('/api/v2/login'));
  check(!/private-refresh|owner-access|bot-access|private-device/.test(exposed),'credential exposed');
  await page.goto(url+'/v2/settings');await page.locator('#recording-toggle').filter({hasText:'記録を開始'}).click();
  await page.request.post(url+'/qa/login/expire',{data:{}});
  await until(s=>s.accounts.broadcaster.state==='connected'&&s.accounts.bot.state==='connected');
  for(let i=0;i<40&&(await get('/qa/state')).refreshes<2;i++)await page.waitForTimeout(500);
  check((await get('/qa/state')).refreshes===2,'not exactly one refresh per account');
  await until(s=>s.accounts.broadcaster.state==='connected'&&s.accounts.bot.state==='connected');
  await page.goto(url+'/v2/connect');
  for(const width of [1440,1024,736,360]){
   await page.setViewportSize({width,height:1000});await page.reload();
   await page.locator('[data-login-role="broadcaster"] .login-state').filter({hasText:'認証を確認'}).waitFor();
   check(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),`overflow ${width}`);
   if(process.env.QA_ARTIFACT_DIR&&[1440,360].includes(width))await page.screenshot({path:`${process.env.QA_ARTIFACT_DIR}/login-${width}.png`,fullPage:true});
  }
  const final=await get('/qa/state');check(final.request_thread_calls.length===0,'request thread called Twitch');
  check(final.sent.length===0,'login sent chat');check(errors.length===0,errors.join(';'));
  console.log(JSON.stringify({login:'both_accounts_verified',automaticRefresh:'one_per_account',recordingInitially:'stopped',tokensInStatus:false,layouts:4,pageErrors:errors}));
 }finally{await browser.close();}
})();
