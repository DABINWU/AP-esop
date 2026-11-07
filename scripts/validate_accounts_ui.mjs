// Shared account UI behavior, no browser rendering or real Cookie emulation.
import fs from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';
class Element{constructor(){this.value='';this.hidden=false;this.textContent='';this.disabled=false;this.open=false;}showModal(){this.open=true;}close(){this.open=false;}reset(){}}
for(const target of ['center','relay']){
  const html=fs.readFileSync(`${target}/static/index.html`,'utf8');
  const elements=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  let logged=false,requests=0,ready=0,exits=0;
  const fetch=async(path,options)=>{
    requests++;assert.equal(options.credentials,'same-origin');
    assert(!options.headers.Authorization,'Browser must never send API keys');
    if(path.endsWith('/login')){assert.equal(options.headers['X-ESOP-Login'],'1');assert.deepEqual(JSON.parse(options.body),{username:'admin',password:'test-password'});logged=true;}
    if(path.endsWith('/password')){assert.equal(options.headers['X-CSRF-Token'],'csrf-1');return {ok:true,json:async()=>({username:'admin',csrf_token:'csrf-2'})};}
    if(path.endsWith('/logout')){assert.equal(options.headers['X-CSRF-Token'],'csrf-2');logged=false;return {ok:true,json:async()=>({logged_out:true})};}
    return {ok:logged,json:async()=>logged?{username:'admin',csrf_token:'csrf-1',expires_at:'2026-11-08T00:00:00Z'}:{error:{message:'Please sign in'}}};
  };
  const context=vm.createContext({document:{getElementById:id=>{assert(elements.has(id),`Missing ${target} auth UI ${id}`);return elements.get(id);}},fetch});
  vm.runInContext(fs.readFileSync('shared/web/accounts.js','utf8'),context);
  const auth=context.browserAuth;
  auth.bind({ready:async()=>{ready++;},logout:()=>{exits++;}});
  await auth.restore();assert.equal(ready,0);assert.equal(exits,1);
  elements.get('username').value='admin';elements.get('password').value='test-password';
  await elements.get('login-form').onsubmit({preventDefault(){}});
  assert.equal(ready,1);assert.equal(auth.csrf,'csrf-1');assert.equal(elements.get('password').value,'');
  await auth.restore();assert.equal(ready,2,'Refresh must recover session without submitting password');
  elements.get('change-password').onclick();assert(elements.get('password-dialog').open);
  elements.get('current-password').value='test-password';elements.get('new-password').value='replacement-password';elements.get('confirm-password').value='mismatch';
  const before=requests;await elements.get('password-form').onsubmit({preventDefault(){}});assert.equal(requests,before);
  elements.get('confirm-password').value='replacement-password';await elements.get('password-form').onsubmit({preventDefault(){}});
  assert.equal(auth.csrf,'csrf-2');assert(!elements.get('password-dialog').open);
  await elements.get('logout').onclick();assert.equal(auth.csrf,'');assert.equal(exits,2);
}
console.log(JSON.stringify({kind:'DOM account behavior, not browser rendering',center:'login/refresh/logout/change-password OK',relay:'login/refresh/logout/change-password OK',browser_key_storage:'none'}));
