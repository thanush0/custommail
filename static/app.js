
const $ = s => document.querySelector(s);
let me = null, folder = "inbox", currentMessage = null, searchTimer = null;
const labels = {inbox:"Inbox",starred:"Starred",sent:"Sent",drafts:"Drafts",spam:"Spam",trash:"Trash"};
async function api(path, opts={}) {
  const headers = {...(opts.headers||{})};
  if (opts.body && !(opts.body instanceof FormData)) headers["Content-Type"]="application/json";
  const r = await fetch(path,{...opts,headers,credentials:"same-origin"});
  let data={}; try { data=await r.json(); } catch {}
  if(!r.ok) throw new Error(data.detail || `Request failed (${r.status})`);
  return data;
}
function showError(id,msg){$(id).textContent=msg||""}
function escapeHtml(s){return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function tab(name){
  document.querySelectorAll(".tab").forEach(b=>b.classList.toggle("active",b.dataset.tab===name));
  $("#loginForm").hidden=name!=="login"; $("#registerForm").hidden=name!=="register";
  $("#authTitle").textContent=name==="login"?"Sign in to your inbox":"Create your mailbox";
  showError("#authError","");
}
document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>tab(b.dataset.tab));
async function enterApp(){
  me=await api("/api/me"); $("#auth").hidden=true; $("#app").hidden=false;
  $("#profileName").textContent=me.display_name||me.email.split("@")[0];
  $("#profileEmail").textContent=me.email; $("#avatar").textContent=(me.display_name||me.email)[0].toUpperCase();
  $("#adminBtn").hidden=!me.is_admin; await loadMessages();
  if(!$("#externalNotice")) {}
  const cfg=await api("/api/config");
  if(cfg.domain) $("#domain").textContent="@"+cfg.domain;
}
$("#loginForm").onsubmit=async e=>{e.preventDefault();showError("#authError","");const d=Object.fromEntries(new FormData(e.target));try{await api("/api/login",{method:"POST",body:JSON.stringify(d)});await enterApp()}catch(x){showError("#authError",x.message)}};
$("#registerForm").onsubmit=async e=>{e.preventDefault();showError("#authError","");const d=Object.fromEntries(new FormData(e.target));try{const r=await api("/api/register",{method:"POST",body:JSON.stringify(d)});await enterApp();notice(`Mailbox created: ${r.email}`)}catch(x){showError("#authError",x.message)}};
async function boot(){try{await enterApp()}catch(e){if(e.message.includes("Sign in")||e.message.includes("Session expired")||e.message.includes("Account unavailable"))return;console.warn(e)}}
function notice(msg){$("#notice").textContent=msg;$("#notice").hidden=false;setTimeout(()=>$("#notice").hidden=true,7000)}
async function loadMessages(){
  $("#folderTitle").textContent=labels[folder];$("#listView").hidden=false;$("#reader").hidden=true;
  document.querySelectorAll(".folder").forEach(b=>b.classList.toggle("active",b.dataset.folder===folder));
  const q=$("#search").value.trim();
  try{const rows=await api(`/api/mail/messages?folder=${encodeURIComponent(folder)}&q=${encodeURIComponent(q)}`);
    $("#countLabel").textContent=`${rows.length} message${rows.length===1?"":"s"}`;
    $("#messageList").innerHTML=rows.length?rows.map(m=>`<div class="message-row ${m.is_read?"":"unread"}" data-id="${m.id}">
      <button class="msg-star ${m.is_starred?"on":""}" data-star="${m.id}" title="Star">${m.is_starred?"★":"☆"}</button>
      <div class="msg-content"><div class="msg-sender">${escapeHtml(folder==="sent"?"To: "+m.recipients:m.sender)}</div><div class="msg-subject"><b>${escapeHtml(m.subject||"(no subject)")}</b> — ${escapeHtml(m.body.slice(0,110))}</div></div>
      <span class="msg-date">${new Date(m.created_at).toLocaleString()}</span></div>`).join(""):`<div class="empty"><b>${q?"No matching messages":"You're all caught up"}</b>${q?"Try a different search.":"Messages sent to your address will appear here."}</div>`;
    $("#messageList").querySelectorAll(".message-row").forEach(el=>el.onclick=ev=>{if(ev.target.closest("[data-star]"))return;openMessage(Number(el.dataset.id))});
    $("#messageList").querySelectorAll("[data-star]").forEach(b=>b.onclick=async ev=>{ev.stopPropagation();try{await api(`/api/mail/messages/${b.dataset.star}/star`,{method:"PATCH"});loadMessages()}catch(x){notice(x.message)}});
  }catch(e){notice(e.message)}
}
async function openMessage(id){
  try{const m=await api(`/api/mail/messages/${id}`);currentMessage=m;$("#listView").hidden=true;$("#reader").hidden=false;
    $("#readSubject").textContent=m.subject||"(no subject)";
    $("#readMeta").textContent=`From ${m.sender} · To ${m.recipients} · ${new Date(m.created_at).toLocaleString()} · ${m.delivery_status}`;
    $("#readBody").textContent=m.body;
    $("#starBtn").textContent=m.is_starred?"★ Starred":"☆ Star";
    $("#readFiles").innerHTML=m.attachments.map(a=>`<a href="/api/mail/attachments/${a.id}">${escapeHtml(a.filename)} (${Math.ceil(a.size/1024)} KB)</a>`).join(" · ");
  }catch(e){notice(e.message)}
}
document.querySelectorAll(".folder").forEach(b=>b.onclick=()=>{folder=b.dataset.folder;loadMessages()});
$("#reload").onclick=$("#refreshBtn").onclick=loadMessages;
$("#search").oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(loadMessages,250)};
$("#backBtn").onclick=loadMessages;
$("#starBtn").onclick=async()=>{if(!currentMessage)return;await api(`/api/mail/messages/${currentMessage.id}/star`,{method:"PATCH"});openMessage(currentMessage.id)};
$("#deleteBtn").onclick=async()=>{if(!currentMessage)return;await api(`/api/mail/messages/${currentMessage.id}`,{method:"DELETE"});notice("Moved to Trash");loadMessages()};
function openCompose(prefill={}){$("#composeModal").hidden=false;const f=$("#composeForm");f.reset();for(const [k,v] of Object.entries(prefill))if(f.elements[k])f.elements[k].value=v;showError("#composeError","")}
$("#composeBtn").onclick=()=>openCompose();$("#closeCompose").onclick=()=>$("#composeModal").hidden=true;
$("#replyBtn").onclick=()=>currentMessage&&openCompose({to:currentMessage.sender,subject:"Re: "+currentMessage.subject,body:`\n\n--- Original message ---\n${currentMessage.body}`});
$("#forwardBtn").onclick=()=>currentMessage&&openCompose({subject:"Fwd: "+currentMessage.subject,body:`\n\n--- Forwarded message ---\nFrom: ${currentMessage.sender}\n\n${currentMessage.body}`});
async function submitCompose(asDraft){
 const f=$("#composeForm"), d=Object.fromEntries(new FormData(f));
 const split=x=>x?x.split(/[;,]/).map(v=>v.trim()).filter(Boolean):[];
 const payload={to:split(d.to),cc:split(d.cc),bcc:split(d.bcc),subject:d.subject,body:d.body,folder:asDraft?"drafts":"sent"};
 try{const r=await api("/api/mail/messages",{method:"POST",body:JSON.stringify(payload)});
   const files=[...($("#composeFiles").files||[])];
   for(const file of files){const fd=new FormData();fd.append("file",file);await api(`/api/mail/attachments/${r.id}`,{method:"POST",body:fd});}
   $("#composeModal").hidden=true; $("#composeFiles").value="";
   notice(asDraft?"Draft saved":`Message status: ${r.status}. Internal recipients delivered: ${r.internal_delivered||0}; external recipients: ${r.external_count||0}.`);
   folder=asDraft?"drafts":"sent";loadMessages();
 }catch(e){showError("#composeError",e.message)}
}
$("#composeForm").onsubmit=e=>{e.preventDefault();submitCompose(false)};
$("#saveDraft").onclick=()=>submitCompose(true);
$("#logoutBtn").onclick=async()=>{await api("/api/logout",{method:"POST"});location.reload()};
$("#adminBtn").onclick=async()=>{try{const [stats,users,audit]=await Promise.all([api("/api/admin/overview"),api("/api/admin/users"),api("/api/admin/audit")]);
 $("#adminStats").innerHTML=Object.entries(stats).map(([k,v])=>`<div class="stat"><b>${v}</b><small>${escapeHtml(k.replace("_"," "))}</small></div>`).join("");
 $("#adminUsers").innerHTML=users.map(u=>`<div class="admin-row"><div><b>${escapeHtml(u.email)}</b><br><small>${u.active?"Active":"Suspended"} ${u.is_admin?"· Admin":""}</small></div>${u.id===0?"":`<button class="secondary" data-toggle="${u.id}">${u.active?"Suspend":"Activate"}</button>`}</div>`).join("");
 $("#adminUsers").querySelectorAll("[data-toggle]").forEach(b=>b.onclick=async()=>{await api(`/api/admin/users/${b.dataset.toggle}/toggle`,{method:"PATCH"});$("#adminBtn").click()});
 $("#auditRows").innerHTML=audit.slice(0,15).map(a=>`<div class="admin-row"><div><b>${escapeHtml(a.action)}</b><br><small>${escapeHtml(a.actor)} · ${new Date(a.created_at).toLocaleString()}</small></div></div>`).join("");
 $("#adminModal").hidden=false;
 }catch(e){notice(e.message)}};
$("#closeAdmin").onclick=()=>$("#adminModal").hidden=true;
boot();
