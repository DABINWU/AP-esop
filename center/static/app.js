'use strict';
const $ = id => document.getElementById(id);
let assets = [], versions = [], current = null, items = [], dirty = false, previewed = false;
let playlists=[], activeModule='production';
let selectedDeployment='', libraryPage=1, deploymentPage=1;
const pageSize=10;
const modules={production:'SOP（生产作业指导书）',inspection:'SIP（检验作业指导书）',check_video:'点检视频',custom:'自定义'};
const previewedModules=new Set();
const categoryOf=item=>item.category || 'custom';
const pageNames={library:'工艺资料库',screens:'屏幕与下发',history:'下发历史'};
function pageFromHash(){
  const route=globalThis.location.hash.slice(1);
  const aliases={playlists:'library',devices:'screens',tasks:'history'};
  return Object.hasOwn(pageNames,route)?route:(Object.hasOwn(aliases,route)?aliases[route]:'library');
}
function showPage(page){
  if($('preview-dialog').open)closePreview();
  for(const id of Object.keys(pageNames)){
    const selected=id===page;
    $('page-'+id).hidden=!selected;
    $('nav-'+id).className=selected?'active':'';
    $('nav-'+id).setAttribute('aria-current',selected?'page':'false');
  }
  document.title=pageNames[page]+' · ESOP中心';
}
globalThis.addEventListener('hashchange',()=>showPage(pageFromHash()));
showPage(pageFromHash());
let poll = null, lastDeployment = null, previewUrls = [], playbackTimer = null, previewGeneration = 0;
let playingItems = [], playingIndex = 0, paused = false;
const labels = {queued:'等待转换', processing:'转换中', ready:'可用', failed:'失败', deleting:'删除未完成', running:'执行中', succeeded:'成功', timed_out:'已超时', published:'已发布', draft:'草稿', online:'在线', offline:'离线', unknown:'未知', playing:'播放', paused:'暂停', idle:'空闲', downloading:'下载中', error:'错误', pending:'待确认'};
function node(tag, text, cls) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; }
function notice(text) { $('notice').textContent = text; $('notice').hidden = !text; }
async function api(path, options = {}) {
  const headers = {...(browserAuth.csrf?{'X-CSRF-Token':browserAuth.csrf}:{}), ...(options.headers || {})};
  if (options.body && !(options.body instanceof FormData)) { headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(options.body); }
  const response = await fetch('/api/v1' + path, {...options, headers,credentials:'same-origin'});
  if(response.status===401)browserAuth.expire();
  if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.error?.message || '请求失败 ' + response.status); }
  return response;
}
async function json(path, options) { return (await api(path, options)).json(); }
function safe(action) { return async (...args) => { try { await action(...args); } catch (e) { notice(e.message); } }; }
function button(text, action) { const b = node('button', text); b.type = 'button'; b.onclick = safe(action); return b; }
function option(value, label) { const n = node('option', label); n.value = value; return n; }
function versionLabel(id) { const v = versions.find(v => v.id === id);const p=playlists.find(p=>p.id===v?.playlist_id); return v ? `${v.name}${p?.task_number?' · '+p.task_number:''}${v.archived_at?'（已删除归档）':''}` : (id ? '历史资料' : '—'); }
function latestPublished(){return playlists.map(p=>({p,v:versions.filter(v=>v.playlist_id===p.id&&v.status==='published'&&!v.archived_at).sort((a,b)=>b.version-a.version)[0]})).filter(row=>row.v);}
function matchesSearch(p,query){return [p.task_number,p.name,p.description].join(' ').toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());}
function paged(rows,page,prefix){const pages=Math.max(1,Math.ceil(rows.length/pageSize));page=Math.max(1,Math.min(page,pages));$(prefix+'-page').textContent=`共 ${rows.length} 条 · 第 ${page} / ${pages} 页`;$(prefix+'-prev').disabled=page===1;$(prefix+'-next').disabled=page===pages;return {page,rows:rows.slice((page-1)*pageSize,page*pageSize)};}

browserAuth.bind({ready:async()=>{
  $('login').hidden = true; $('workspace').hidden = false; $('logout').hidden = false;
  await refresh(); clearInterval(poll); poll = setInterval(safe(refreshStatus), 10000);
},logout:()=>{clearInterval(poll);closePreview();$('workspace').hidden=true;$('login').hidden=false;$('logout').hidden=true;notice('');}});
async function refresh() {
  const [a, v, config] = await Promise.all([json('/assets'), json('/playlists'), json('/settings')]);
  assets = a.assets; acceptVersions(v); renderPlaylists(); renderVersionSelectors(); renderAssets();
  $('limits').textContent = `图片 / Excel / PDF ≤ ${Math.round(config.upload_max_bytes/1048576)} MB；MP4 ≤ ${Math.round(config.video_max_bytes/1048576)} MB / ${config.video_max_seconds}秒`;
  await refreshStatus(); notice('');
}
async function refreshStatus() {
  const [d, t, a, v] = await Promise.all([json('/devices'), json('/deployments'), json('/assets'),json('/playlists')]);
  assets = a.assets; acceptVersions(v); renderPlaylists(); renderVersionSelectors(); renderEditor(); renderAssets(); renderDevices(d); renderTasks(t.tasks);
}
function acceptVersions(data){
  playlists=data.playlists; versions=data.versions;
  if(!current)return;
  if(!playlists.some(p=>p.id===current.playlist_id)){chooseVersion(null);return;}
  const next=versions.find(v=>v.id===current.id);if(!next){chooseVersion(null);return;}
  if(next.archived_at){const replacement=versions.filter(v=>v.playlist_id===current.playlist_id&&!v.archived_at).sort((a,b)=>b.version-a.version)[0];chooseVersion(replacement);return;}
  const latest=versions.filter(v=>v.playlist_id===current.playlist_id&&!v.archived_at).sort((a,b)=>b.version-a.version)[0];
  if(!dirty&&current.status==='published'&&latest&&latest.version>current.version){chooseVersion(latest);return;}
  if(JSON.stringify(next.items)!==JSON.stringify(current.items)){
    if(dirty){const known=new Set(current.items.map(i=>i.id));items.push(...structuredClone(next.items.filter(i=>!known.has(i.id))));}
    else items=structuredClone(next.items);
    previewed=false;previewedModules.clear();
  }
  current=next;
}
function renderPlaylists(){
  $('playlist-list').replaceChildren();
  const result=paged(playlists.filter(p=>matchesSearch(p,$('library-search').value)),libraryPage,'library');libraryPage=result.page;
  if(!result.rows.length){const tr=node('tr'),td=node('td',playlists.length?'没有匹配的资料。':'资料库还没有内容，请先填写上方表单。');td.colSpan=4;tr.append(td);$('playlist-list').append(tr);}
  for(const p of result.rows){const tr=node('tr'),name=node('td'),actions=node('td');name.append(node('strong',p.name));if(p.description)name.append(node('small',p.description));
    const list=versions.filter(v=>v.playlist_id===p.id&&!v.archived_at).sort((a,b)=>b.version-a.version);
    actions.append(button('进入资料',()=>{if(dirty&&!confirm('放弃未保存改动并切换资料条目？'))return;chooseVersion(list[0]);renderVersionSelectors();$('editor').scrollIntoView({block:'start',behavior:'smooth'});}));
    const remove=button('删除',async()=>{if(!confirm(`删除资料条目“${p.name}”？\n活动屏幕引用会阻止删除；发布历史会归档保留；无发布历史的专属文件将清理。`))return;const result=await json('/playlists/'+p.id,{method:'DELETE'});if(current?.playlist_id===p.id)chooseVersion(null);await refresh();notice(result.cleanup_pending.length?'资料条目已归档，部分文件清理未完成，系统会重试。':(result.retained_history?'资料条目已删除归档，发布历史及文件保留。':'资料条目及无引用专属文件已删除。'));});remove.className='danger';actions.append(remove);
    tr.append(name,node('td',p.task_number || '—'),node('td',list[0]?.status==='draft'?(list.some(v=>v.status==='published')?'有待发布修改':'待发布'):'已发布'),actions);$('playlist-list').append(tr);
  }
}
$('refresh').onclick = safe(refresh);
$('upload-form').onsubmit = safe(async e => {
  e.preventDefault(); if(!current || current.status!=='draft')throw new Error('请先创建资料，或点击“编辑资料”进入编辑');
  if(dirty)await saveDraft();
  const versionId=current.id,category=activeModule,files=[...$('upload-file').files]; $('upload-button').disabled = true;
  let uploaded=0;
  try {for(const file of files){const data=new FormData();data.append('file',file);data.append('duration_seconds',$('upload-seconds').value);await json('/versions/'+versionId+'/modules/'+category+'/assets',{method:'POST',body:data});uploaded++;} $('upload-file').value = ''; await refresh();notice(`${uploaded} 个文件已上传到${modules[category]}，文档转换完成后自动加入。`); }
  catch(error){await refreshStatus().catch(()=>{});throw new Error(`已成功上传 ${uploaded} 个文件；${error.message}`);}
  finally { $('upload-button').disabled = false; }
});
function renderAssets() {
  const expanded=new Set([...document.querySelectorAll('#asset-list details[open]')].map(n=>n.dataset.assetId));
  $('asset-list').replaceChildren();
  const ids=new Set(items.filter(i=>categoryOf(i)===activeModule).map(i=>i.file.id));
  const visible=assets.filter(a=>a.files.some(f=>ids.has(f.id)) || (a.metadata.attach_version_id===current?.id && a.metadata.category===activeModule && a.status!=='ready'));
  $('asset-summary').textContent = `${modules[activeModule]} · ${visible.length} 份文件`;
  if (!visible.length) { const tr=node('tr'),td=node('td','本模块暂无文件，请直接上传。'); td.colSpan=7; tr.append(td); $('asset-list').append(tr); }
  for (const a of visible) {
    const tr=node('tr'),name=node('td'),kind=node('td'),state=node('td'),info=node('td'),notes=node('td'),actions=node('td',undefined,'asset-actions');
    name.append(node('strong',a.original_name));
    const original = a.files.find(f => ['original','image','video'].includes(f.role));
    kind.append(node('span',({excel:'Excel',pdf:'PDF',image:'图片',video:'视频'})[a.kind] || a.kind));
    if(original)kind.append(node('small',formatBytes(original.size_bytes)));
    state.append(node('span',labels[a.status] || a.status,'tag'));
    if(a.conversion_job && a.status!=='deleting') state.append(node('small',`转换：${labels[a.conversion_job.status] || a.conversion_job.status} · 尝试 ${a.conversion_job.attempts} 次`));
    if(a.metadata.page_count)info.append(node('p',`${a.metadata.page_count} 页 · `+(a.metadata.render_mode==='screen_16_9'?'16:9 · 1920×1080':`${a.metadata.render_dpi} DPI · 原分页`)));
    if(a.kind==='video' && a.status==='ready')info.append(node('p',`${a.metadata.duration_seconds.toFixed(2)} 秒 · ${a.metadata.width}×${a.metadata.height} · H.264 / ${a.metadata.audio_codecs.join(',') || '无音轨'}`));
    if(a.kind==='image' && a.metadata.width)info.append(node('p',`${a.metadata.width}×${a.metadata.height}`));
    if(!info.childNodes.length)info.textContent='—';
    if(a.warnings.length){const detail=node('details');detail.dataset.assetId=a.id;detail.open=expanded.has(a.id);detail.append(node('summary',`提示 (${a.warnings.length})`));for(const w of a.warnings)detail.append(node('p',w,'warning'));notes.append(detail);}
    if(a.error)notes.append(node('p',a.error,'failure'));
    if(!notes.childNodes.length)notes.textContent='—';
    if (a.status === 'ready') actions.append(button('分页预览', () => previewAsset(a)));
    const pdf = a.files.find(f => f.role === 'pdf');
    if (pdf && a.status==='ready') actions.append(button('PDF 预览', async () => { openPreview(a.original_name + ' · PDF'); const generation = previewGeneration; const url = await mediaUrl(pdf); if (generation !== previewGeneration) return; const frame = node('iframe'); frame.src = url; frame.title = 'PDF预览'; $('preview-body').append(frame); }));
    if (original && a.status!=='deleting') actions.append(button('下载原件', async () => { const response = await api('/files/' + original.id); const url = URL.createObjectURL(await response.blob()); const link = node('a'); link.href = url; link.download = a.original_name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 2000); }));
    actions.append(button('查看引用',async()=>{const usage=await json('/assets/'+a.id+'/usage');notice(usage.can_delete?'此资料没有保存的播放列表引用，可以删除。':usage.blockers.join('\n'));}));
    const remove=button(a.status==='deleting'?'重试删除':'删除',()=>removeAsset(a));remove.className='danger';actions.append(remove);
    tr.append(name,kind,state,info,node('td',new Date(a.created_at).toLocaleString('zh-CN',{hour12:false})),notes,actions);$('asset-list').append(tr);
  }
}
function formatBytes(value){if(value<1024)return value+' B';if(value<1048576)return (value/1024).toFixed(1)+' KB';return (value/1048576).toFixed(1)+' MB';}
async function removeAsset(asset){
  if(!current || current.status!=='draft'){notice('资料已发布，请先点击“编辑资料”，再删除文件。');return;}
  if(!confirm(`删除“${asset.original_name}”及本草稿内的全部分页？\n其他版本仍引用时只移出当前草稿，保留历史文件。`))return;
  if(dirty)await saveDraft();
  try{const result=await json('/versions/'+current.id+'/assets/'+asset.id,{method:'DELETE'});await refreshStatus();notice(result.message);}
  catch(error){await refreshStatus().catch(()=>{});throw error;}
}
function renderVersionSelectors() {
  const selected=current?.id || $('version-select').value;
  $('version-select').replaceChildren(option('', '选择资料'));
  for(const v of versions){if(!v.archived_at&&v.playlist_id===current?.playlist_id)$('version-select').append(option(v.id,v.name));}
  $('version-select').value=selected;renderDeploymentTable();
}
function renderDeploymentTable(){
  const available=latestPublished();
  if(selectedDeployment&&!available.some(row=>row.v.id===selectedDeployment)){selectedDeployment='';notice('所选资料已更新或删除，请重新选择最新已发布内容。');}
  const chosen=available.find(row=>row.v.id===selectedDeployment);
  $('deploy-selected').textContent=chosen?'已选择：'+chosen.p.name+' · '+(chosen.p.task_number||''):'尚未选择资料';
  $('deploy').disabled=!chosen;
  const result=paged(available.filter(row=>matchesSearch(row.p,$('deploy-search').value)),deploymentPage,'deploy');deploymentPage=result.page;
  $('deploy-list').replaceChildren();
  for(const {p,v} of result.rows){const tr=node('tr'),choice=node('td'),radio=node('input');radio.type='radio';radio.name='deployment-choice';radio.value=v.id;radio.checked=v.id===selectedDeployment;radio.setAttribute('aria-label','选择 '+p.name);radio.onchange=()=>{selectedDeployment=v.id;renderDeploymentTable();};choice.append(radio);
    const title=node('td',p.name);if(p.description)title.append(node('small',p.description));
    const newer=versions.some(x=>x.playlist_id===p.id&&!x.archived_at&&x.status==='draft'&&x.version>v.version);
    tr.className=radio.checked?'selected-row':'';tr.append(choice,title,node('td',p.task_number||'—'),node('td',v.published_at?new Date(v.published_at).toLocaleString():'—'),node('td',String(v.items.length)),node('td',newer?'已发布 · 有待发布修改':'已发布'));$('deploy-list').append(tr);}
  if(!result.rows.length){const tr=node('tr'),td=node('td',available.length?'没有匹配的资料。':'暂无可下发资料，请先在工艺资料库发布。');td.colSpan=6;tr.append(td);$('deploy-list').append(tr);}
}
$('library-search').oninput=()=>{libraryPage=1;renderPlaylists();};
$('deploy-search').oninput=()=>{deploymentPage=1;renderDeploymentTable();};
$('library-prev').onclick=()=>{libraryPage--;renderPlaylists();};$('library-next').onclick=()=>{libraryPage++;renderPlaylists();};
$('deploy-prev').onclick=()=>{deploymentPage--;renderDeploymentTable();};$('deploy-next').onclick=()=>{deploymentPage++;renderDeploymentTable();};
$('playlist-form').onsubmit = safe(async e => { e.preventDefault();if(dirty)await saveDraft(); const v = await json('/playlists',{method:'POST',body:{name:$('playlist-name').value,description:$('task-description').value}});$('playlist-form').reset();await refresh();chooseVersion(v);renderVersionSelectors();notice('资料条目已创建，编号：'+v.task_number); });
$('version-select').onchange = () => { if (dirty && !confirm('放弃未保存的草稿改动？')) { $('version-select').value = current.id; return; } chooseVersion(versions.find(v => v.id === $('version-select').value)); };
function chooseVersion(v) { current = v || null; items = structuredClone(v?.items || []); dirty = false; previewed = false; previewedModules.clear();activeModule=Object.keys(modules).find(category=>items.some(i=>categoryOf(i)===category)) || 'production';renderEditor();renderAssets(); }
function changed() { dirty = true; previewed = false; previewedModules.clear();renderEditor();renderAssets(); }
function renderEditor() {
  $('editor').hidden = !current; if (!current) return;
  const draft = current.status === 'draft'; $('editor-title').textContent = `${versionLabel(current.id)} · ${labels[current.status]}`;
  $('upload-form').hidden = !draft; $('save-version').hidden = !draft; $('publish-version').hidden = !draft;
  $('publish-version').disabled = !previewed || dirty || !items.length;
  $('edit-library').hidden=draft;
  $('editor-hint').textContent = draft ? (dirty ? '有未保存排序或秒数改动。' : '请预览所有有内容的模块后发布；转换中或失败文件会阻止发布。') : '资料已发布。需要继续维护时，请点击“编辑资料”。';
  $('module-tabs').replaceChildren();
  for(const [category,label] of Object.entries(modules)){const count=items.filter(i=>categoryOf(i)===category).length;const tab=button(`${label} (${count})`,()=>{activeModule=category;$('upload-file').value='';renderEditor();renderAssets();});tab.setAttribute('aria-pressed',String(activeModule===category));if(activeModule===category)tab.className='selected';$('module-tabs').append(tab);}
  $('module-title').textContent=modules[activeModule];$('upload-file').accept=activeModule==='check_video'?'.mp4':'.xlsx,.xls,.pdf,.png,.jpg,.jpeg,.mp4';
  $('item-list').replaceChildren();
  items.forEach((item, index) => {
    if(categoryOf(item)!==activeModule)return;
    const li = node('li', undefined, 'item'); const asset = assets.find(a => a.files.some(f => f.id === item.file.id));
    li.append(node('span', `${index+1}. ${asset?.original_name || item.type}${item.file.page_number ? ' · 第'+item.file.page_number+'页' : ''}`, 'label'));
    if (item.type !== 'video') { const input = node('input'); input.type = 'number'; input.min = 1; input.max = 3600; input.value = item.duration_seconds; input.disabled = !draft; input.setAttribute('aria-label', '停留秒数'); input.onchange = () => { const value = Number(input.value); if (value < 1 || value > 3600 || !Number.isFinite(value)) { notice('图片停留时间须为1–3600秒'); input.value = item.duration_seconds; return; } item.duration_seconds = value; changed(); }; li.append(input, node('span', '秒')); }
    else li.append(node('span', '完整播放一次'));
    if (draft) {const indexes=items.map((i,n)=>categoryOf(i)===activeModule?n:-1).filter(n=>n>=0),local=indexes.indexOf(index); li.append(button('↑', () => { if(local>0){const other=indexes[local-1];[items[other],items[index]]=[items[index],items[other]];changed();} }), button('↓', () => { if(local+1<indexes.length){const other=indexes[local+1];[items[other],items[index]]=[items[index],items[other]];changed();} }), button('移除此页', () => { items.splice(index,1); changed(); })); }
    $('item-list').append(li);
  });
}
async function saveDraft() {
  if (!current || current.status !== 'draft') return;
  const v = await json('/versions/'+current.id+'/items', {method:'PUT',body:{items:items.map(i => ({file_id:i.file.id,duration_seconds:i.duration_seconds,category:categoryOf(i)}))}});
  versions = versions.map(old => old.id===v.id?v:old); current = v; items = structuredClone(v.items); dirty = false; previewed = false;previewedModules.clear(); renderEditor();
}
$('save-version').onclick = safe(saveDraft);
$('clone-version').onclick = safe(async () => { if (!current) throw new Error('先选择版本'); if (dirty && !confirm('复制已保存版本并放弃未保存改动？')) return; const v = await json('/versions/'+current.id+'/clone',{method:'POST'}); versions.unshift(v); chooseVersion(v); renderVersionSelectors(); });
$('edit-library').onclick=safe(async()=>{
  if(!current || current.status!=='published')return;
  let draft=versions.filter(v=>v.playlist_id===current.playlist_id&&v.status==='draft'&&!v.archived_at&&v.version>current.version).sort((a,b)=>b.version-a.version)[0];
  if(!draft){draft=await json('/versions/'+current.id+'/clone',{method:'POST'});versions.unshift(draft);}
  chooseVersion(draft);renderVersionSelectors();renderPlaylists();notice('已进入资料编辑，原下发内容保持不变。');
});
$('delete-version').onclick=safe(async()=>{
  if(!current)throw new Error('请先选择版本');
  if(!confirm(`删除 ${versionLabel(current.id)}？\n未保存修改会放弃。屏幕或运行任务引用会阻止删除；发布历史将归档保留。至少保留一个版本。`))return;
  const playlistId=current.playlist_id;
  const result=await json('/versions/'+current.id,{method:'DELETE'});
  chooseVersion(null);await refresh();
  chooseVersion(versions.filter(v=>v.playlist_id===playlistId&&!v.archived_at).sort((a,b)=>b.version-a.version)[0]);renderVersionSelectors();
  notice(result.cleanup_pending.length?'版本已删除，部分文件清理未完成，系统会重试。':(result.retained_history?'版本已归档，发布清单与历史文件保留。':'草稿版本及无其他引用的文件已删除。'));
});
$('preview-version').onclick = safe(async () => { if (!current || !items.some(i=>categoryOf(i)===activeModule)) throw new Error('当前模块没有可播放内容'); if (dirty) await saveDraft();previewedModules.add(activeModule);previewed=items.every(i=>previewedModules.has(categoryOf(i))); renderEditor(); openPreview(versionLabel(current.id) + ' · '+modules[activeModule]+' · 循环预览'); playingItems = structuredClone(items.filter(i=>categoryOf(i)===activeModule)); playingIndex = 0; paused = false; $('playback-controls').hidden = false; await playItem(); });
$('publish-version').onclick = safe(async () => { if (!previewed || dirty) throw new Error('请先保存并预览'); const v = await json('/versions/'+current.id+'/publish',{method:'POST'}); versions = versions.map(old => old.id===v.id?v:old); chooseVersion(v); renderVersionSelectors();renderPlaylists();notice('资料已发布，可在屏幕与下发中选择。'); });

$('device-form').onsubmit = safe(async e => { e.preventDefault(); await json('/devices',{method:'POST',body:{device_id:$('device-id').value,name:$('device-name').value}}); $('device-id').value=''; $('device-name').value=''; await refreshStatus(); });
function renderDevices(data) {
  const selected = new Set([...document.querySelectorAll('.device-check:checked')].map(n => n.value)); $('stats').replaceChildren();
  for (const [field,label] of [['registered','登记屏幕'],['online','在线'],['offline','离线'],['unknown','未知'],['pending','待同步']]) { const n = node('div',label,'stat'); n.append(node('strong',data.summary[field])); $('stats').append(n); }
  $('relay-state').textContent = (data.relay.state==='online'?'中转通信正常':'中转失联 / 未连接，车间状态未知') + ' · 最后更新（UTC）：' + (data.relay.last_seen_at || '尚无记录');
  $('device-list').replaceChildren();
  for (const d of data.devices) {
    const tr = node('tr'), first = node('td'), check = node('input'); check.type='checkbox'; check.className='device-check'; check.value=d.device_id; check.checked=selected.has(d.device_id); first.append(check,node('span',' '+d.name),node('small',d.device_id)); tr.append(first);
    tr.append(node('td',labels[d.online_state]+(d.pending?' · 待同步':'')));
    const desired = node('td',versionLabel(d.desired?.playlist_version_id)); desired.append(node('small',d.desired?'revision '+d.desired.assignment_revision:'未安排')); tr.append(desired);
    const actual = node('td',versionLabel(d.actual_version_id)); actual.append(node('small',d.current_item_id || '无当前项')); tr.append(actual);
    const state = node('td',labels[d.playback_state] || d.playback_state); state.append(node('small','待更新：'+versionLabel(d.pending_version_id)));if(d.download_progress){const p=d.download_progress;state.append(node('small',`终端下载 ${p.completed_files}/${p.total_files} · ${Math.round(p.received_bytes*100/Math.max(1,p.total_bytes))}%`));} tr.append(state);
    const last = node('td',d.last_contact_at || '未联系'); if (d.error) last.append(node('small',d.error,'failure')); tr.append(last); $('device-list').append(tr);
  }
}
$('select-all').onchange = () => document.querySelectorAll('.device-check').forEach(n => n.checked=$('select-all').checked);
async function sendDeployment() {
  try { const result = await json('/deployments',{method:'POST',body:lastDeployment}); $('retry-deploy').hidden=true; notice('任务已创建：'+result.id); await refreshStatus(); }
  catch(e) { $('retry-deploy').hidden=false; throw e; }
}
$('deploy').onclick = safe(async () => { const device_ids=[...document.querySelectorAll('.device-check:checked')].map(n=>n.value); if (!device_ids.length || !selectedDeployment) throw new Error('请选择资料和屏幕'); const picked=selectedDeployment;acceptVersions(await json('/playlists'));renderVersionSelectors();if(selectedDeployment!==picked)throw new Error('所选资料已更新，请重新选择后下发');lastDeployment={request_id:globalThis.crypto?.randomUUID?.() || 'web_'+Date.now()+'_'+Math.random().toString(36).slice(2),playlist_version_id:selectedDeployment,device_ids,timeout_seconds:Number($('deploy-timeout').value)}; await sendDeployment(); });
$('retry-deploy').onclick = safe(sendDeployment);
function renderTasks(tasks) {
  $('task-list').replaceChildren(); if (!tasks.length) $('task-list').append(node('p','暂无执行记录。'));
  for (const task of tasks) { const n = node('div',undefined,'task'); n.append(node('strong',`${labels[task.status]} · ${versionLabel(task.playlist_version_id)}`),node('small',` · ${task.created_at} · ${task.targets.length}台`)); const details=node('details'); details.append(node('summary',task.id)); details.append(node('p',`截止 ${task.expires_at}；结束 ${task.finished_at || '—'}`)); for(const t of task.targets) details.append(node('p',`${t.device_id} · revision ${t.assignment_revision} · ${labels[t.status] || t.status}${t.error?' · '+t.error:''}`)); n.append(details); $('task-list').append(n); }
}

function closePreview() { previewGeneration++; clearTimeout(playbackTimer); const video=$('preview-body').querySelector('video'); if(video) video.pause(); $('preview-dialog').close(); $('preview-body').classList.remove('screen-playback');$('preview-body').replaceChildren(); for(const url of previewUrls) URL.revokeObjectURL(url); previewUrls=[]; playingItems=[]; }
function openPreview(title) { closePreview(); $('preview-title').textContent=title; $('playback-controls').hidden=true; $('preview-dialog').showModal(); }
$('close-preview').onclick=closePreview; $('preview-dialog').addEventListener('cancel', e => {e.preventDefault();closePreview();});
async function mediaUrl(file) { const response=await api('/files/'+file.id); const url=URL.createObjectURL(await response.blob()); previewUrls.push(url); return url; }
async function previewAsset(asset) {
  openPreview(asset.original_name); const generation=previewGeneration;
  for (const f of asset.files.filter(f=>['page','image','video'].includes(f.role)).sort((a,b)=>(a.page_number||0)-(b.page_number||0))) {
    const url=await mediaUrl(f); if(generation!==previewGeneration) return;
    const n=node('div',undefined,'page-preview'), media=node(f.role==='video'?'video':'img'); media.src=url;
    if(f.role==='video') media.controls=true; else media.alt=asset.original_name+' '+(f.page_number||'');
    n.append(media,node('p',f.page_number?'第 '+f.page_number+' 页':f.media_type)); $('preview-body').append(n);
  }
}
async function playItem() {
  clearTimeout(playbackTimer); if (!playingItems.length) return; const generation=++previewGeneration;
  const old=$('preview-body').querySelector('video'); if(old) old.pause(); $('preview-body').replaceChildren(node('p','加载预览…'));
  for(const url of previewUrls) URL.revokeObjectURL(url); previewUrls=[];
  const item=playingItems[playingIndex], url=await mediaUrl(item.file); if(generation!==previewGeneration) return;
  const media=node(item.type==='video'?'video':'img'); media.src=url; $('preview-body').classList.add('screen-playback');$('preview-body').replaceChildren(media);
  $('preview-position').textContent=`${playingIndex+1} / ${playingItems.length} · ${item.type==='video'?'播放一次':item.duration_seconds+'秒'} · 最后一项后循环`;
  if(item.type==='video') { media.controls=true; media.onended=()=>safe(nextItem)(); media.onerror=()=>notice('浏览器无法解码此视频，请检查客户端兼容性'); if(!paused) { try { await media.play(); } catch { notice('请点击视频播放按钮开始预览'); } } }
  else { media.alt='播放项 '+(playingIndex+1); if(!paused) playbackTimer=setTimeout(safe(nextItem),item.duration_seconds*1000); }
}
async function nextItem() { if(!playingItems.length)return; playingIndex=(playingIndex+1)%playingItems.length; await playItem(); }
$('next-preview').onclick=safe(nextItem);
$('pause-preview').onclick=safe(async()=>{ paused=!paused; clearTimeout(playbackTimer); const video=$('preview-body').querySelector('video'); if(video) { if(paused)video.pause();else await video.play(); } else if(!paused)playbackTimer=setTimeout(safe(nextItem),playingItems[playingIndex].duration_seconds*1000); });

browserAuth.restore();
