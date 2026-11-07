'use strict';
globalThis.browserAuth=(()=>{
  const el=id=>document.getElementById(id);
  let csrf='',ready=async()=>{},loggedOut=()=>{};
  const message=text=>{el('auth-message').textContent=text;};
  async function request(path,body,method='POST'){
    const response=await fetch('/api/v1/auth/'+path,{method,credentials:'same-origin',cache:'no-store',
      headers:{'Content-Type':'application/json','X-ESOP-Login':'1',...(csrf?{'X-CSRF-Token':csrf}:{})},
      ...(body===undefined?{}:{body:JSON.stringify(body)})});
    const data=await response.json();
    if(!response.ok)throw new Error(data.error?.message||'登录请求失败');
    return data;
  }
  async function accept(data){csrf=data.csrf_token;el('account-name').textContent=data.username;el('change-password').hidden=false;message('');await ready();}
  function expire(){csrf='';el('account-name').textContent='';el('change-password').hidden=true;loggedOut();}
  function bind(callbacks){
    ready=callbacks.ready;loggedOut=callbacks.logout;
    el('login-form').onsubmit=async event=>{
      event.preventDefault();const button=el('login-button');button.disabled=true;
      try{await accept(await request('login',{username:el('username').value,password:el('password').value}));el('password').value='';}
      catch(error){message(error.message);}finally{button.disabled=false;}
    };
    el('logout').onclick=async()=>{try{await request('logout');expire();message('已退出登录');}catch(error){message(error.message);}};
    el('change-password').onclick=()=>{el('password-form').reset();el('password-message').textContent='';el('password-dialog').showModal();};
    el('close-password').onclick=()=>{el('password-form').reset();el('password-dialog').close();};
    el('password-form').onsubmit=async event=>{
      event.preventDefault();
      if(el('new-password').value!==el('confirm-password').value){el('password-message').textContent='两次新密码不一致';return;}
      const button=el('password-button');button.disabled=true;
      try{const data=await request('password',{current_password:el('current-password').value,new_password:el('new-password').value});
        csrf=data.csrf_token;el('password-form').reset();el('password-dialog').close();message('密码已修改，其他登录已退出。');}
      catch(error){el('password-message').textContent=error.message;}finally{button.disabled=false;}
    };
  }
  async function restore(){try{await accept(await request('session',undefined,'GET'));}catch{expire();}}
  return {bind,restore,expire,get csrf(){return csrf;}};
})();
