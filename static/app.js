let drives=[], stats=null, currentDrive=null, currentPath="", scanTimer=null, verifyTimer=null;
const $=s=>document.querySelector(s);
const fmt=n=>{if(n==null)return "—";const u=["B","KB","MB","GB","TB"];let i=0;let v=n;while(v>=1024&&i<u.length-1){v/=1024;i++}return i==0?v+" B":v.toFixed(1).replace(".",",")+" "+u[i]};
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const pct=(a,b)=>b?Math.round(a/b*100):0;
function toast(m){const t=$("#toast");t.textContent=m;t.style.display="block";setTimeout(()=>t.style.display="none",3500)}
async function api(p,opt={}){const r=await fetch(p,{headers:{"Content-Type":"application/json"},...opt});const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||("Erro "+r.status));return j}
function showView(name){document.querySelectorAll(".view").forEach(v=>v.hidden=true);const el=$("#view-"+name);if(el)el.hidden=false;document.querySelectorAll("#nav a").forEach(a=>a.classList.toggle("active",a.dataset.route===name))}
function route(){const h=(location.hash||"#/visao-geral").replace("#/","")||"visao-geral";showView(h);
  if(h==="visao-geral")loadOverview(); if(h==="explorador")loadExplorer(); if(h==="duplicatas")loadDuplicates();
  if(h==="confirmacao")loadVerify(); if(h==="planejamento")loadPlanForm();}
window.addEventListener("hashchange",route);

async function loadOverview(){
  try{const s=await api("/api/stats");stats=s;
    $("#stats").innerHTML=`
    <div class="card stat"><span>Capacidade total catalogada</span><b>${fmt(s.total_bytes)}</b><span>${s.drives_count} drive(s) · ${s.connected_count} conectado(s) agora</span></div>
    <div class="card stat"><span>Espaço usado</span><b>${fmt(s.used_bytes)}</b><span>${pct(s.used_bytes,s.total_bytes)}% do total</span></div>
    <div class="card stat"><span>Espaço disponível</span><b>${fmt(s.free_bytes)}</b><span>livre para uso</span></div>
    <div class="card stat"><span>Arquivos catalogados</span><b>${s.files_count}</b><span>${s.duplicate_groups} grupo(s) de possíveis duplicatas · economia potencial ${fmt(s.potential_saving)}</span></div>`;
  }catch(e){$("#server-msg").innerHTML=`<div class="notice err"><b>Servidor indisponível.</b> Rode o atalho <b>iniciar-painel</b> novamente. Se o problema persistir, confira se a porta 8765 já está em uso por outro painel.</div>`;return}
  const d=await api("/api/drives");drives=d.drives;
  $("#overview-empty").innerHTML=drives.length?"":`<div class="empty"><b>Nenhum drive catalogado ainda</b>Conecte seu primeiro HD ou SSD, clique em <b>Detectar drives</b> abaixo e depois em <b>Catalogar</b>. O inventário é só leitura — nada é apagado ou movido.<br><br><a class="btn primary" href="#/como-usar">Ver o passo a passo →</a></div>`;
  $("#drive-cards").innerHTML=drives.map(dr=>{
    const total=dr.total_bytes_live||dr.total_bytes||0, used=dr.used_bytes_live??dr.used_bytes??0, free=dr.free_bytes_live??dr.free_bytes??0;
    const p=pct(used,total);
    return `<div class="card drive-card"><div class="row between"><h3 style="margin:0">💾 ${esc(dr.name_catalog||"Drive sem nome")}</h3>
      ${dr.connected?'<span class="badge ok">● Conectado agora</span>':'<span class="badge off">○ Desconectado</span>'}</div>
      <div class="small">${esc(dr.physical_label?("Etiqueta: "+dr.physical_label):"Sem etiqueta física")} · ${esc(dr.media_type||"Tipo não identificado")} · ${esc(dr.fstype||"—")}</div>
      <div class="bar"><i class="${p>85?"hot":""}" style="width:${p}%"></i></div>
      <div class="small">${fmt(used)} usados de ${fmt(total)} · ${fmt(free)} livres (${p}%)</div>
      <dl class="kv"><dt>Volume</dt><dd>${esc(dr.volume_label||"—")} <span class="mono">${esc(dr.mountpoint||"")}</span></dd>
      <dt>UUID</dt><dd><span class="mono">${esc((dr.persistent_id||"").slice(0,36))}</span>${dr.is_ambiguous?' <span class="badge warn">Identidade ambígua</span>':""}</dd>
      <dt>Finalidade</dt><dd>${esc(dr.purpose||"—")}</dd><dt>Localização</dt><dd>${esc(dr.location||"—")}</dd>
      <dt>Última atualização</dt><dd>${dr.last_inventory_at?esc(new Date(dr.last_inventory_at).toLocaleString("pt-BR")):"Nunca — faça o primeiro inventário"}</dd></dl>
      ${dr.is_ambiguous?`<div class="notice warn" style="font-size:13px">${esc(dr.ambiguity_reason||"Identidade ambígua.")}</div>`:""}
      <div class="row"><button class="btn primary sm" onclick="startScan(${dr.id})" ${dr.connected?"":"disabled"}>📋 Fazer inventário</button>
      ${dr.connected?`<button class="btn sm" onclick="openDrive(${dr.id})">📂 Abrir drive</button>`:""}
      <button class="btn sm" onclick="openEdit(${dr.id})">✏️ Editar</button>
      <button class="btn sm" onclick="goExplorer(${dr.id})">🔍 Explorar</button></div>
      <div class="small" style="margin-top:8px">A data acima é a do inventário — não confunda com a data de modificação dos arquivos.</div></div>`}).join("");
}
async function detectDrives(){
  $("#detect-info").textContent="Detectando…";
  try{const d=await api("/api/system/drives");
    const vols=(d.volumes||[]).filter(v=>!v._error);
    $("#detect-info").textContent=`${d.os} · ${vols.length} volume(s) com sistema de arquivos detectado(s).`;
    $("#detected").innerHTML=vols.length?vols.map(v=>`<div class="card" style="padding:12px;margin-bottom:8px"><div class="row between"><div><b>${esc(v.volume_label||v.mountpoint||v.device||"Volume")}</b> <span class="tag">${esc(v.fstype||"")}</span> ${v.is_external?'<span class="badge info">Externo</span>':'<span class="badge off">Interno / sistema</span>'} <span class="badge ${v.media_type==="Tipo não identificado"?"warn":"ok"}">${esc(v.media_type)}</span><br><span class="small mono">${esc(v.device||"")} · ${esc(v.mountpoint||"sem montagem")} · UUID ${esc((v.persistent_id||"").slice(0,36))} · ${fmt(v.total_bytes)}</span>${v.is_ambiguous?`<br><span class="small" style="color:var(--amber)">⚠ ${esc(v.ambiguity_reason||"")}</span>`:""}</div>
    <button class="btn primary sm" onclick='registerDetected(${JSON.stringify(v).replace(/'/g,"&#39;")})' ${v.mountpoint?"":"disabled"}>Catalogar</button></div></div>`).join(""):`<div class="empty"><b>Nenhum volume externo detectado agora</b>Conecte o HD/SSD, espere ele aparecer no gerenciador de arquivos do sistema e clique em <b>Detectar drives</b> de novo.</div>`;
  }catch(e){toast(e.message)}
}
async function registerDetected(v){
  try{const r=await api("/api/drives/register",{method:"POST",body:JSON.stringify({persistent_id:v.persistent_id,device:v.device,mountpoint:v.mountpoint,volume_label:v.volume_label,fstype:v.fstype,total_bytes:v.total_bytes,used_bytes:v.used_bytes,free_bytes:v.free_bytes,disk_id:v.disk_id,media_type:v.media_type,is_ambiguous:v.is_ambiguous,ambiguity_reason:v.ambiguity_reason,name:v.volume_label||v.mountpoint})});
    toast("Drive catalogado. Agora faça o inventário.");loadOverview();}
  catch(e){toast(e.message);if(e.message.includes("já está"))loadOverview();}
}
async function catalogFolder(){
  const path=$("#folder-path").value.trim(), name=$("#folder-name").value.trim();
  if(!path)return toast("Informe o caminho da pasta.");
  try{await api("/api/drives/catalog-folder",{method:"POST",body:JSON.stringify({path,name})});toast("Pasta catalogada.");$("#folder-path").value="";loadOverview();}
  catch(e){toast(e.message)}
}
async function startScan(id){
  try{await api(`/api/drives/${id}/scan`,{method:"POST",body:"{}"});toast("Leitura iniciada — acompanhe no Explorador. Quando terminar, você poderá ejetar o drive.");location.hash="#/explorador";currentDrive=id;loadExplorer();}
  catch(e){toast(e.message)}
}
async function openDrive(id){try{const r=await api(`/api/drives/${id}/open`,{method:"POST",body:"{}"});toast(r.note||"Abrindo o drive no gerenciador de arquivos…")}catch(e){toast(e.message)}}
function goExplorer(id){currentDrive=id;location.hash="#/explorador";loadExplorer()}
let editingId=null;
function openEdit(id){const d=drives.find(x=>x.id===id);if(!d)return;editingId=id;$("#edit-name").value=d.name_catalog||"";$("#edit-label").value=d.physical_label||"";$("#edit-purpose").value=d.purpose||"";$("#edit-location").value=d.location||"";$("#modal").classList.add("open")}
function closeModal(){$("#modal").classList.remove("open")}
async function saveEdit(){try{await api(`/api/drives/${editingId}`,{method:"PUT",body:JSON.stringify({name_catalog:$("#edit-name").value,physical_label:$("#edit-label").value,purpose:$("#edit-purpose").value,location:$("#edit-location").value})});closeModal();toast("Catálogo atualizado — o volume e os arquivos não foram alterados.");loadOverview()}catch(e){toast(e.message)}}

async function loadExplorer(){
  const d=await api("/api/drives");drives=d.drives;
  const sel=$("#explorer-drive");
  sel.innerHTML=drives.map(x=>`<option value="${x.id}" ${x.id===currentDrive?"selected":""}>${esc(x.name_catalog)} ${x.connected?"●":"○"} ${x.physical_label?("· "+esc(x.physical_label)):""}</option>`).join("")||`<option value="">Nenhum drive catalogado</option>`;
  if(!currentDrive&&drives.length)currentDrive=drives[0].id;
  if(!currentDrive){$("#file-list").innerHTML=`<div class="empty"><b>Nenhum drive para explorar</b>Catalogue o primeiro drive na Visão geral.</div>`;$("#treemap").innerHTML="";$("#breadcrumb").innerHTML="";return}
  sel.value=String(currentDrive);
  await renderExplorer();
  clearInterval(scanTimer);scanTimer=setInterval(pollScan,1500);
}
function explorerDriveChanged(){currentDrive=parseInt($("#explorer-drive").value);currentPath="";renderExplorer()}
async function renderExplorer(){
  const dr=drives.find(x=>x.id===currentDrive);if(!dr)return;
  $("#btn-rescan").disabled=!dr.connected;
  $("#btn-rescan").title=dr.connected?"Reler o inventário deste drive (preserva etiquetas e histórico)":"Disponível só com o volume correto conectado";
  const parts=currentPath?currentPath.split("/"):[""];
  $("#breadcrumb").innerHTML=`<button onclick="navTo('')">🏠 ${esc(dr.name_catalog)}</button>`+parts.filter(Boolean).map((p,i)=>` / <button onclick="navTo('${esc(parts.slice(0,i+1).join("/"))}')">${esc(p)}</button>`).join("");
  const b=await api(`/api/drives/${currentDrive}/browse?path=${encodeURIComponent(currentPath)}`);
  if(!b.has_inventory){$("#file-list").innerHTML=`<div class="empty"><b>Sem inventário ainda</b>Clique em <b>Reler este drive</b> com o drive conectado para fazer a primeira leitura (só nomes, tamanhos e datas — sem ler o conteúdo).</div>`;$("#treemap").innerHTML="";return}
  $("#file-list").innerHTML=b.items.length?`<table><tr><th>Nome</th><th>Tamanho</th><th>Modificado em</th><th></th></tr>${b.items.map(it=>`<tr><td>${it.is_dir?"📁":"📄"} ${it.is_dir?`<button style="background:none;border:0;color:var(--teal-d);font-weight:700" onclick="navTo('${esc(it.rel_path)}')">${esc(it.name)}</button>`:esc(it.name)}${it.is_symlink?' <span class="tag">link</span>':""}</td><td>${it.is_dir?fmt(it.folder_size||0):fmt(it.size)}</td><td>${it.mtime?new Date(it.mtime).toLocaleString("pt-BR"):"—"}</td><td class="small">${it.is_dir?(it.folder_files||0)+" arquivo(s)":""}</td></tr>`).join("")}</table>`:`<div class="empty">Pasta vazia no inventário.</div>`;
  const t=await api(`/api/drives/${currentDrive}/treemap`);
  const colors=["#0f2a44","#0d9488","#2563eb","#7c3aed","#b45309","#15803d","#be185d","#475569"];
  const tot=t.folders.reduce((a,f)=>a+f.size,0)||1;
  $("#treemap").innerHTML=t.folders.length?t.folders.map((f,i)=>`<div style="background:${colors[i%colors.length]};flex-grow:${Math.max(1,Math.round(f.size/tot*20))}" onclick="navTo('${esc(f.name)}')" title="${esc(f.name)} — ${fmt(f.size)}"><span>${esc(f.name)}</span><small>${fmt(f.size)} · ${Math.round(f.size/tot*100)}%</small></div>`).join(""):`<span class="muted">Sem pastas com arquivos ainda.</span>`;
}
function navTo(p){currentPath=p;renderExplorer()}
async function pollScan(){
  if(!currentDrive)return;
  try{const s=await api(`/api/drives/${currentDrive}/scan/status`);
    const live=s.live, sc=s.scan;
    if(!sc){$("#scan-progress").innerHTML="";return}
    if(sc.status==="running"&&live){
      $("#scan-progress").innerHTML=`<div class="notice"><b>📋 Leitura em andamento…</b> ${live.files||0} arquivos · ${live.dirs||0} pastas · ${fmt(live.bytes||0)}<br><span class="small mono">${esc(live.current_path||"")}</span><div class="progress" style="margin-top:8px"><i style="width:${Math.min(100,(live.files||0)%100)}%"></i></div><div class="row" style="margin-top:8px"><button class="btn sm danger" onclick="cancelScan()">■ Interromper leitura</button><span class="small">Interromper mantém o último inventário válido.</span></div></div>`;
    }else if(sc.status==="completed"){
      $("#scan-progress").innerHTML=`<div class="notice"><b>✅ Leitura concluída.</b> ${sc.files_count} arquivos e ${sc.dirs_count} pastas catalogados. <b>Já pode ejetar o drive pelo sistema operacional.</b>${sc.incomplete?' <br>⚠ Leitura incompleta — alguns itens não puderam ser lidos (veja os erros no inventário).':""}</div>`;
      if($("#scan-progress").dataset.done!=String(sc.id)){$("#scan-progress").dataset.done=sc.id;renderExplorer();}
    }else if(sc.status==="interrupted"){
      $("#scan-progress").innerHTML=`<div class="notice warn"><b>⏸ Leitura interrompida.</b> O último inventário válido foi mantido. Reconecte o drive e clique em <b>Reler este drive</b>.</div>`;
    }}catch(e){}
}
async function cancelScan(){try{await api(`/api/drives/${currentDrive}/scan/cancel`,{method:"POST",body:"{}"});toast("Interrupção solicitada.")}catch(e){toast(e.message)}}
async function rescanCurrent(){if(!currentDrive)return;await startScan(currentDrive)}
async function globalSearch(q){
  if(!q||q.length<2){$("#search-results").innerHTML="";return}
  const r=await api(`/api/search?q=${encodeURIComponent(q)}`);
  $("#search-results").innerHTML=r.results.length?`<div class="notice"><b>${r.results.length} resultado(s) em todos os inventários</b> — inclusive drives desconectados. Para abrir o arquivo, reconecte o drive indicado.</div><table><tr><th>Arquivo</th><th>Drive / Etiqueta</th><th>Tamanho</th><th>Caminho</th></tr>${r.results.slice(0,50).map(x=>`<tr><td>${x.is_dir?"📁":"📄"} ${esc(x.name)}</td><td>${esc(x.name_catalog)} ${x.physical_label?`· ${esc(x.physical_label)}`:""}</td><td>${fmt(x.size)}</td><td class="mono">${esc(x.rel_path)}</td></tr>`).join("")}</table>`:`<div class="notice warn">Nenhum resultado para “${esc(q)}” nos inventários atuais.</div>`;
}

async function loadDuplicates(){
  const q=$("#dup-search")?$("#dup-search").value:"";
  const d=await api(`/api/duplicates?q=${encodeURIComponent(q)}`);
  $("#dup-pairs").innerHTML=d.pairs.length?`<p class="small"><b>Pares de drives com mais correspondências:</b> ${d.pairs.slice(0,5).map(p=>`${esc(p.name_a)} ↔ ${esc(p.name_b)} (<b>${p.matches}</b>)`).join(" · ")}</p>`:`<p class="small">Sem pares com correspondências ainda.</p>`;
  $("#dup-total").innerHTML=d.groups.length?`<div class="card"><b>${d.groups.length} grupo(s)</b> · economia potencial total <b>${fmt(d.total_potential)}</b> <span class="small">(potencial — manter uma cópia é decisão sua; não é espaço garantido)</span></div>`:"";
  $("#dup-list").innerHTML=d.groups.length?d.groups.map((g,i)=>`<div class="card"><div class="row between"><div><b>#${i+1} ${esc(g.name)}</b> · ${fmt(g.size)} cada · ${g.count} cópias</div><span class="badge warn">Economia potencial: ${fmt(g.potential_saving)}</span></div>
    ${g.copies.map(c=>`<div class="row" style="margin-top:6px"><span class="pill">💾 ${esc(c.name_catalog)} ${c.physical_label?("· "+esc(c.physical_label)):""}</span><span class="mono">${esc(c.rel_path)}</span><button class="btn sm" onclick="openInExplorer(${c.drive_id},'${esc(c.parent_path||"")}')">Abrir pasta no catálogo →</button></div>`).join("")}</div>`).join(""):`<div class="card"><div class="empty"><b>Nenhuma possível duplicata encontrada</b>Faça o inventário de pelo menos dois drives (ou de pastas com arquivos repetidos) para comparar.</div></div>`;
}
function openInExplorer(driveId,parent){currentDrive=driveId;currentPath=parent||"";location.hash="#/explorador";loadExplorer()}

async function loadVerify(){
  const v=await api("/api/verify/status");
  const s=v.state;
  $("#verify-info").innerHTML=`<p class="muted">Fila: <b>${v.queue_files} arquivo(s)</b> com tamanhos repetidos · <b>${fmt(v.queue_bytes)}</b> para ler por completo. ${v.queue_bytes>10*1024*1024*1024?'<span class="badge warn">Fila grande — a leitura integral pode demorar bastante. Só inicie se tiver tempo.</span>':""} Estado: <b>${esc(s.status)}</b></p>`;
  $("#verify-progress").innerHTML=s.total_files?`<div class="progress"><i style="width:${s.total_files?Math.round(s.hashed_files/s.total_files*100):0}%"></i></div><p class="small">${s.hashed_files} de ${s.total_files} verificados · ${fmt(s.hashed_bytes)} de ${fmt(s.total_bytes)} · atual: ${esc(s.current||"—")}${s.note?`<br>⚠ ${esc(s.note)}`:""}</p>`:"";
  $("#confirmed-list").innerHTML=v.confirmed.length?v.confirmed.map(g=>`<div style="border-top:1px solid var(--line);padding:10px 0"><b>${g.count} cópias confirmadas</b> · ${fmt(g.size)} cada · economia potencial ${fmt(g.potential_saving)} · <span class="mono">${esc(g.sha256.slice(0,16))}…</span>${g.copies.map(c=>`<div class="small">💾 ${esc(c.name_catalog)} ${c.physical_label?("· "+esc(c.physical_label)):""} — <span class="mono">${esc(c.rel_path)}</span></div>`).join("")}</div>`).join(""):`<div class="empty"><b>Nenhuma duplicata confirmada ainda</b>Inicie a verificação acima para comparar o conteúdo.</div>`;
  const d=await api("/api/duplicates");
  $("#pending-list").innerHTML=d.groups.length?d.groups.slice(0,10).map(g=>`<div class="small" style="padding:6px 0;border-top:1px solid var(--line)">⏳ <b>${esc(g.name)}</b> · ${g.count} cópias · ${fmt(g.size)} · economia potencial ${fmt(g.potential_saving)}</div>`).join(""):`<p class="muted">Sem candidatos pendentes.</p>`;
  clearInterval(verifyTimer);verifyTimer=setInterval(async()=>{if(!$("#view-confirmacao").hidden)loadVerify()},3000);
}
async function verifyStart(){try{const r=await api("/api/verify/start",{method:"POST",body:"{}"});toast(r.message||"Verificação iniciada.")}catch(e){toast(e.message)}}
async function verifyPause(){await api("/api/verify/pause",{method:"POST",body:"{}"});toast("Verificação pausada — resultados preservados.")}
async function verifyResume(){await api("/api/verify/resume",{method:"POST",body:"{}"});toast("Verificação retomada.")}
async function verifyCancel(){await api("/api/verify/cancel",{method:"POST",body:"{}"});toast("Verificação interrompida — resultados preservados.")}

async function loadPlanForm(){
  const d=await api("/api/drives");drives=d.drives;
  $("#plan-source").innerHTML=drives.map(x=>`<option value="${x.id}">${esc(x.name_catalog)} ${x.physical_label?("· "+esc(x.physical_label)):""}</option>`).join("")||`<option value="">Nenhum drive catalogado</option>`;
}
async function runPlan(){
  const src=$("#plan-source").value, margin=$("#plan-margin").value;
  if(!src)return toast("Escolha um drive para liberar.");
  try{const p=await api(`/api/plan?source_id=${src}&margin_percent=${margin}`);
    $("#plan-result").innerHTML=`
    <div class="notice">${esc(p.note)}</div>
    <h3>Destinos (maiores primeiro)</h3>
    ${p.destinations.map(d=>`<div class="card" style="padding:12px"><b>${esc(d.name)}</b> ${d.label?`· ${esc(d.label)}`:""}<div class="bar"><i style="width:${d.pct_after}%"></i></div><span class="small">Ocupação prevista: ${d.pct_after}% · ${fmt(d.used_after)} usados · ${fmt(d.free_after)} livres após a simulação · margem mantida: ${fmt(d.margin_bytes)}<br>Receberia: ${d.plan.length?d.plan.map(esc).join(", "):"nada"}</span></div>`).join("")||"<p>Nenhum destino com espaço conhecido.</p>"}
    <h3>Distribuição simulada</h3>
    ${p.allocations.length?`<table><tr><th>Pasta / item</th><th>Tamanho</th><th>Destino simulado</th></tr>${p.allocations.map(a=>`<tr><td>${esc(a.item)}</td><td>${fmt(a.size)}</td><td>${esc(a.dest_name)}</td></tr>`).join("")}</table>`:"<p class='muted'>Nada coube nos destinos com a margem escolhida.</p>"}
    ${p.unplaced.length?`<div class="notice warn"><b>Sem destino (não cabem com a margem atual):</b> ${p.unplaced.map(u=>esc(u.name)+" ("+fmt(u.size)+")").join(", ")} — considere outro drive, reduzir a margem ou liberar espaço antes.</div>`:""}`;
  }catch(e){toast(e.message);$("#plan-result").innerHTML=`<div class="notice err">${esc(e.message)}</div>`}
}
function exportData(kind,fmt){window.open(`/api/export?kind=${kind}&format=${fmt}`,"_blank")}
route();
setInterval(()=>{if(!$("#view-visao-geral").hidden)loadOverview()},15000);
