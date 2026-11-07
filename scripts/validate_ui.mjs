// Dependency-free DOM smoke test, not browser/visual validation.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

class Element {
  constructor(tag='div'){this.tagName=tag;this.childNodes=[];this.dataset={};this.value='';this.hidden=false;this.attributes={};this.own='';this.classList={add(){},remove(){}};}
  append(...children){this.childNodes.push(...children);}
  replaceChildren(...children){this.own='';this.childNodes=[...children];}
  set textContent(value){this.own=String(value);this.childNodes=[];}
  get textContent(){return this.own+this.childNodes.map(c=>c.textContent || '').join('');}
  setAttribute(name,value){this.attributes[name]=String(value);}
  addEventListener(){}
  querySelector(){return null;}
  reset(){}
  scrollIntoView(){}
}
const html=fs.readFileSync('center/static/index.html','utf8');
const nodes=new Map([...html.matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)].map(m=>{const element=new Element();element.hidden=/\shidden(?:\s|>|=)/.test(m[0]);return [m[1],element];}));
const document={getElementById(id){assert(nodes.has(id),'Missing element '+id);return nodes.get(id);},createElement:tag=>new Element(tag),querySelectorAll:()=>[]};
const events={};
const location={hash:''};
const context=vm.createContext({document,location,browserAuth:{csrf:'test-csrf',bind(){},restore(){},expire(){}},addEventListener:(name,handler)=>{events[name]=handler;},structuredClone,console,setTimeout,clearTimeout,setInterval,clearInterval,URL,confirm:()=>false});
vm.runInContext(fs.readFileSync('center/static/app.js','utf8'),context);
function assertPage(page){
  for(const id of ['library','screens','history']){
    assert.equal(nodes.get('page-'+id).hidden,id!==page);
    assert.equal(nodes.get('nav-'+id).attributes['aria-current'],id===page?'page':'false');
  }
}
assertPage('library');
const asset=(id,category)=>({id,original_name:id+'.png',kind:'image',status:'ready',created_at:'2026-11-06T00:00:00Z',warnings:[],error:null,
  metadata:{width:1280,height:720,playlist_id:'p',category,attach_version_id:'v'},files:[{id:'f-'+id,role:'image',size_bytes:512}]});
const assets=[asset('production-file','production'),asset('custom-file','custom')];
const items=assets.map((a,i)=>({id:'i'+i,position:i,type:'image',asset_id:a.id,category:a.metadata.category,duration_seconds:20,file:a.files[0]}));
const version={id:'v',playlist_id:'p',name:'Task fixture',version:1,status:'draft',items};
vm.runInContext(`assets=${JSON.stringify(assets)};playlists=[{id:'p',name:'Task fixture',task_number:'QA'}];versions=[${JSON.stringify(version)}];chooseVersion(versions[0]);renderPlaylists();renderVersionSelectors();`,context);
assert.equal(nodes.get('module-tabs').childNodes.length,4);
assert(nodes.get('module-title').textContent.includes('生产'));
assert(nodes.get('asset-list').textContent.includes('production-file.png'));
assert(!nodes.get('asset-list').textContent.includes('custom-file.png'));
await nodes.get('module-tabs').childNodes[3].onclick();
assert(nodes.get('module-title').textContent.includes('自定义'));
assert(nodes.get('asset-list').textContent.includes('custom-file.png'));
assert(!nodes.get('asset-list').textContent.includes('production-file.png'));
vm.runInContext('chooseVersion({...versions[0],status:"published"})',context);
assert.equal(nodes.get('upload-form').hidden,true);
assert.equal(nodes.get('version-controls').hidden,true);
assert.equal(nodes.get('version-help').hidden,true);
assert.equal(nodes.get('edit-library').hidden,false);
let cloneCalls=0;
context.fetch=async(path,options)=>{
  assert.equal(path,'/api/v1/versions/v/clone');assert.equal(options.method,'POST');cloneCalls++;
  return {ok:true,json:async()=>({...version,id:'working-draft',version:2,status:'draft'})};
};
await nodes.get('edit-library').onclick();
assert.equal(cloneCalls,1);
assert.equal(vm.runInContext('current.id',context),'working-draft');
assert.equal(nodes.get('upload-form').hidden,false);
vm.runInContext('chooseVersion({...versions.find(v=>v.id==="v"),status:"published"})',context);
await nodes.get('edit-library').onclick();
assert.equal(cloneCalls,1,'Existing working draft must be reused');
vm.runInContext('chooseVersion({...versions[0],items:versions[0].items.map(({category,...item})=>item)})',context);
assert(nodes.get('module-title').textContent.includes('自定义'));
assert.equal(nodes.get('upload-form').hidden,false);
assert.equal(nodes.get('version-controls').hidden,true);
assert.equal(nodes.get('edit-library').hidden,true);
vm.runInContext('versions.push({...versions[0],id:"archived-version",version:2,status:"published",archived_at:"2026-11-06T01:00:00Z"});renderVersionSelectors();',context);
assert(!nodes.get('version-select').childNodes.some(n=>n.value==='archived-version'));
assert(!nodes.get('deploy-list').textContent.includes('archived-version'));
vm.runInContext("dirty=true;items[0].duration_seconds=37",context);
nodes.get('upload-file').value='pending-selection.png';
nodes.get('device-name').value='未提交的屏幕名称';
for(const [hash,page] of [['#screens','screens'],['#history','history'],['#library','library'],['#tasks','history'],['#devices','screens'],['#playlists','library'],['#constructor','library']]){
  location.hash=hash;events.hashchange();assertPage(page);
  assert.equal(vm.runInContext('dirty',context),true);
  assert.equal(vm.runInContext('items[0].duration_seconds',context),37);
  assert.equal(nodes.get('upload-file').value,'pending-selection.png');
  assert.equal(nodes.get('device-name').value,'未提交的屏幕名称');
}
location.hash='#history';vm.runInContext('showPage(pageFromHash())',context);assertPage('history');
vm.runInContext(`dirty=false;playlists=Array.from({length:23},(_,i)=>({id:'catalog-'+i,name:'Product '+i,task_number:'ESOP-'+i}));versions=playlists.flatMap((p,i)=>[1,2].map(n=>({id:p.id+'-published-'+n,playlist_id:p.id,name:p.name,version:n,status:'published',published_at:'2026-11-07T00:00:00Z',items:[]})));renderPlaylists();renderVersionSelectors();`,context);
assert.equal(nodes.get('deploy-list').childNodes.length,10);
assert.equal(nodes.get('playlist-list').childNodes.length,10);
assert(nodes.get('deploy-page').textContent.includes('23'));
assert(!nodes.get('playlist-list').textContent.includes('个版本'));
assert.equal(nodes.get('deploy-list').childNodes[0].childNodes[0].childNodes[0].value,'catalog-0-published-2');
nodes.get('deploy-list').childNodes[0].childNodes[0].childNodes[0].onchange();
assert.equal(vm.runInContext('selectedDeployment',context),'catalog-0-published-2');
assert.equal(nodes.get('deploy').disabled,false);
nodes.get('deploy-next').onclick();assert(nodes.get('deploy-page').textContent.includes('2 / 3'));
nodes.get('deploy-search').value='Product 22';nodes.get('deploy-search').oninput();
assert.equal(nodes.get('deploy-list').childNodes.length,1);
assert.equal(vm.runInContext('selectedDeployment',context),'catalog-0-published-2');
vm.runInContext(`versions.push({...versions[0],id:'new-publication',version:3});renderVersionSelectors();`,context);
assert.equal(vm.runInContext('selectedDeployment',context),'');
assert.equal(nodes.get('deploy').disabled,true);
nodes.get('library-search').value='ESOP-22';nodes.get('library-search').oninput();
assert.equal(nodes.get('playlist-list').childNodes.length,1);
assert(!vm.runInContext("versionLabel('new-publication')",context).includes('v3'));
vm.runInContext(`versions.push({...versions.find(v=>v.playlist_id==='catalog-22'),id:'unpublished-work',version:4,status:'draft'});renderDeploymentTable();`,context);
assert(nodes.get('deploy-list').textContent.includes('有待发布修改'));
assert.equal(nodes.get('deploy-list').childNodes[0].childNodes[0].childNodes[0].value,'catalog-22-published-2');
nodes.get('deploy-list').childNodes[0].childNodes[0].childNodes[0].onchange();
document.querySelectorAll=selector=>selector==='.device-check:checked'?[{value:'screen-a'}]:[];
let deploymentPosts=0;
context.fetch=async(path,options)=>{
  if(path==='/api/v1/playlists')return {ok:true,json:async()=>JSON.parse(vm.runInContext(`JSON.stringify({playlists,versions:[...versions,{...versions.find(v=>v.playlist_id==='catalog-22'),id:'just-published',version:5,status:'published'}]})`,context))};
  deploymentPosts++;throw new Error('Unexpected request '+path);
};
await nodes.get('deploy').onclick();
assert.equal(deploymentPosts,0,'Must not deploy a superseded selection');
assert.equal(vm.runInContext('selectedDeployment',context),'',nodes.get('notice').textContent);
console.log(JSON.stringify({kind:'DOM smoke, not browser rendering',boot:'ok',four_modules:'ok',scoped_files:'ok',legacy_custom:'ok',published_upload:'hidden',exclusive_pages:'ok',navigation_preserves_edits:'ok',legacy_and_unknown_routes:'ok',archived_versions_filtered:'ok',version_controls:'hidden',published_edit_and_draft_reuse:'ok',latest_only_search_pagination:'ok',superseded_selection_blocked:'ok'}));
