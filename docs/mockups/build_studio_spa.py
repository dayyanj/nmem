"""Project the design mockup (studio-wizard.html) into the SERVED wizard SPA
(src/nmem/agent_core/studio_ui/index.html).

Anti-drift, by the same logic as the capability catalog: the mockup is the single source
of truth for the wizard's markup + style; this script wraps it as a standalone HTML document
and swaps the mockup's *simulated* <script> for one wired to the live /studio/* endpoints
(catalog fetched, Test/fetch-models/Create posted). Re-run after editing the mockup:

    python docs/mockups/build_studio_spa.py

The wired <script> lives here (not in the mockup) so the mockup stays a pure, openable
design artifact and the runtime behaviour has one home.
"""
import pathlib

HERE = pathlib.Path(__file__).resolve()
REPO = HERE.parents[2]                       # .../nmem
MOCKUP = HERE.parent / "studio-wizard.html"
OUT = REPO / "src" / "nmem" / "agent_core" / "studio_ui" / "index.html"

# The live-wired script that replaces the mockup's simulated one.
SCRIPT = r"""<script>
// ── state (catalog is fetched from the backend, never inlined — one source of truth) ──
let CATALOG = null, CAPS = {}, enabled = new Set(), preset = null;
let GROUP_ORDER = ["drives","concerns","goals","learning","comms","recall","graph","prediction","obligations","meta"];
const GROUP_LABEL = {drives:"Drives", concerns:"Concerns", goals:"Goals", learning:"Experiential learning",
  comms:"Surprise to communication", recall:"Recall", graph:"World-model graph",
  prediction:"Prediction & hypotheses", obligations:"Obligations", meta:"Meta / self-improvement"};
const TEMPLATES = {
  researcher:{nm:"Scout", aid:"scout", preset:"reflective",
    obj:"Continuously build an accurate model of your domain from primary sources.\nNever let an unknown drive a guessed answer. Seek it out and verify.",
    ent:"your domain, its primary sources"},
  critic:{nm:"Critic", aid:"critic", preset:"reflective",
    obj:"Challenge the reasoning you're given: surface the missed assumption and the unpriced risk.\nOffer a genuinely different view; agree plainly when it's warranted.\nSeek and verify rather than confabulate.",
    ent:"the work you review, the domain you reason about"},
  support:{nm:"Ada", aid:"ada", preset:"memory",
    obj:"Answer grounded in the product knowledge base; never invent capabilities.\nEscalate honestly when you can't verify something.\nBe concise and specific.",
    ent:"the product, its documentation, common user tasks"},
  assistant:{nm:"Assistant", aid:"assistant", preset:"full_cognition", autonomy:"tiered",
    obj:"Help me get things done: look things up, draft, and use the tools you're given.\nAsk before anything irreversible; act directly on the routine.\nRemember how I like things done and apply it next time.",
    ent:"my tools, my recurring tasks, how I prefer things done",
    tool:{_type:"webhook", name:"get_weather", method:"GET",
          url:"https://api.example.com/weather?city={city}",
          parameters:{type:"object", properties:{city:{type:"string"}}}}},
  analyst:{nm:"Watcher", aid:"watcher", preset:"reflective",
    obj:"Continuously watch your domain and notice what changed.\nSurface what matters, and why, before it's asked for.\nSeek out the unknown rather than guess at it.",
    ent:"the domain you watch, its sources and signals"},
  teammate:{nm:"Teammate", aid:"teammate", preset:"full_cognition", hive:"shared_world",
    obj:"Contribute to a shared understanding alongside other agents.\nDo your part and keep your own goals; build on what the others learn.",
    ent:"the shared domain, the teammates you work alongside"},
  companion:{nm:"Companion", aid:"companion", preset:"reflective",
    obj:"Be a thoughtful companion who remembers our conversations.\nListen first, and pick up where we left off.\nNotice how things are going for me over time.",
    ent:"our conversations, what matters to you, how things change over time"},
};

// ── capability graph (dependency-safe toggling; mirrors config_writer's closure) ──
function addWithDeps(f,acc){if(acc.has(f))return;acc.add(f);(CAPS[f]?.r||[]).forEach(r=>addWithDeps(r,acc));}
function dependentsOf(f){return CATALOG.caps.filter(c=>(c.r||[]).includes(f)).map(c=>c.f);}
function removeWithDependents(f,acc){if(!acc.has(f))return;acc.delete(f);dependentsOf(f).forEach(d=>removeWithDependents(d,acc));}
function toggle(flag){
  preset=null;
  const before=new Set(enabled);
  if(enabled.has(flag)) removeWithDependents(flag,enabled); else addWithDeps(flag,enabled);
  render();
  [...enabled].filter(f=>!before.has(f)&&f!==flag).forEach(f=>{const el=document.querySelector(`[data-flag="${f}"]`);
    if(el){el.classList.add('flash');setTimeout(()=>el.classList.remove('flash'),800);}});
}
function selectPreset(name){preset=name;const acc=new Set();(CATALOG.presets[name].flags||[]).forEach(f=>addWithDeps(f,acc));enabled=acc;render();}
function render(){renderPresets();renderGroups();renderEnv();syncPresets();document.getElementById('ec').textContent=enabled.size+" on";}
function syncPresets(){document.querySelectorAll('#presets .pick').forEach(b=>b.setAttribute('aria-pressed',b.dataset.p===preset));}
function renderPresets(){const host=document.getElementById('presets');if(host.dataset.done)return;host.dataset.done=1;
  for(const[k,p]of Object.entries(CATALOG.presets)){const acc=new Set();(p.flags||[]).forEach(f=>addWithDeps(f,acc));
    const b=document.createElement('button');b.className='pick';b.dataset.p=k;b.setAttribute('aria-pressed','false');b.onclick=()=>selectPreset(k);
    b.innerHTML=`<div class="nm">${p.label}</div><div class="bl">${p.blurb}</div><div class="cnt">${acc.size} capabilities</div>`;host.appendChild(b);}}
function renderGroups(){const host=document.getElementById('groups');host.innerHTML='';
  for(const g of GROUP_ORDER){const caps=CATALOG.caps.filter(c=>c.g===g);if(!caps.length)continue;
    const on=caps.filter(c=>enabled.has(c.f)).length;const wrap=document.createElement('div');wrap.className='grp';
    wrap.innerHTML=`<h3>${GROUP_LABEL[g]||g}<span class="ln"></span><span style="font-family:var(--mono);color:${on?'var(--accent)':'var(--muted)'}">${on}/${caps.length}</span></h3>`;
    const pills=document.createElement('div');pills.className='pills';
    caps.forEach(c=>{const ison=enabled.has(c.f);const p=document.createElement('div');p.className='pill';p.dataset.flag=c.f;
      p.setAttribute('role','switch');p.setAttribute('aria-pressed',ison);p.tabIndex=0;p.onclick=()=>toggle(c.f);
      p.onkeydown=e=>{if(e.key===' '||e.key==='Enter'){e.preventDefault();toggle(c.f);}};
      const short=c.f.replace(/^NMEM_(SYM_)?/,'');
      const dep=ison&&(c.r||[]).length?`<span class="depmark" title="pulls in ${c.r.join(', ')}">+dep</span>`:'';
      const sub=c.sub?`<div class="sub"><b>needs:</b> ${c.sub}</div>`:'';
      p.innerHTML=`${dep}<div class="fl">${short}</div><div class="ds">${c.s}</div>${sub}`;pills.appendChild(p);});
    wrap.appendChild(pills);host.appendChild(wrap);}}
// env preview mirrors config_writer.render_capabilities_env exactly: booleans 'true',
// DRIVES_OUTWARD_ACTIONS carries its CSV value, and RECALL_DRIVE auto-writes RECALL_AGENT_ID.
function renderEnv(){const on=[...enabled].sort();
  let html=`<span class="c"># capabilities.env, generated by nmem.agent_core.config_writer\n# dependency-complete · booleans only · defaults omitted\n</span>`;
  if(!on.length)html+=`<span class="c"># (nothing on yet: a blank mind)</span>`;
  let lastG=null;
  on.forEach(f=>{const g=CAPS[f]?.g;if(g!==lastG){html+=`<span class="c">\n# ${GROUP_LABEL[g]||g}\n</span>`;lastG=g;}
    const val=f==='NMEM_SYM_DRIVES_OUTWARD_ACTIONS'?'explore':'true';
    html+=`<span class="k">${f}</span>=<span class="v">${val}</span>\n`;});
  if(enabled.has('NMEM_SYM_RECALL_DRIVE_ENABLED')){const aid=(document.getElementById('aid').value||'agent').trim();
    html+=`<span class="c">\n# values\n</span><span class="k">NMEM_SYM_RECALL_AGENT_ID</span>=<span class="v">${aid}</span>\n`;}
  document.getElementById('env').innerHTML=html;}
function renderTemplates(){const host=document.getElementById('tmpl');
  const meta={
    researcher:["Researcher","Learns a domain from primary sources; verifies rather than guesses."],
    critic:["Critic","Challenges reasoning and surfaces the missed assumption or unpriced risk — an independent second opinion."],
    support:["Support responder","Answers grounded in a knowledge base and escalates honestly."],
    assistant:["Assistant","Gets things done with the tools you give it — the hands-on agent."],
    analyst:["Analyst / watcher","Watches a domain and surfaces what changed and why it matters."],
    teammate:["Team member","Joins a shared world-model with other agents (a hive)."],
    companion:["Companion","A reflective companion that remembers your context over time."]};
  for(const[k,[nm,bl]]of Object.entries(meta)){const b=document.createElement('button');b.className='pick';b.dataset.t=k;
    b.setAttribute('aria-pressed',k==='researcher');b.onclick=()=>applyTemplate(k);
    b.innerHTML=`<div class="nm">${nm}</div><div class="bl">${bl}</div>`;host.appendChild(b);}}
// applyTemplate seeds a COHERENT, runnable starting point: persona + the matching capability preset,
// the autonomy tier, an example tool (so the actor step isn't empty), and hive mode — not just text.
function applyTemplate(k){const t=TEMPLATES[k];
  document.getElementById('nm').value=t.nm;document.getElementById('aid').value=t.aid;
  document.getElementById('obj').value=t.obj;document.getElementById('ent').value=t.ent;
  document.querySelectorAll('#tmpl .pick').forEach(b=>b.setAttribute('aria-pressed',b.dataset.t===k));
  if(t.preset&&CATALOG)selectPreset(t.preset);                 // matching capability set
  const au=document.getElementById('autonomy'); if(au)au.value=t.autonomy||'read_only';
  TOOLS=TOOLS.filter(x=>!x._seeded);                           // drop a prior template's example tool
  if(t.tool)TOOLS.push({...t.tool,_seeded:true});             // seed this one's (if any)
  renderToolChips();
  const hm=document.getElementById('hive_mode'); if(hm){hm.value=t.hive||'isolated';onHiveMode();}
  if(CATALOG)renderEnv();}
function onName(){document.getElementById('aid').value=document.getElementById('nm').value.trim().toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_|_$/g,'');if(CATALOG)renderEnv();}
function onEmb(){document.getElementById('embremote').hidden=document.getElementById('emb').value!=='remote';}
function setView(v){document.getElementById('basic').hidden=v!=='basic';document.getElementById('advanced').hidden=v!=='advanced';
  document.getElementById('tabBasic').setAttribute('aria-pressed',v==='basic');document.getElementById('tabAdv').setAttribute('aria-pressed',v==='advanced');}

// ── providers (client-side hints only: base_url + key env + model suggestions) ──
const PROVIDERS={
  local:{label:"Local · vLLM / Ollama / LM Studio",base:"http://127.0.0.1:8000/v1",dialect:"generic",key:false,models:["your-model"]},
  openai:{label:"OpenAI",base:"https://api.openai.com/v1",dialect:"openai",key:true,keyenv:"OPENAI_API_KEY",models:["gpt-4o","gpt-4o-mini","o4-mini"]},
  anthropic:{label:"Anthropic · Claude",base:"https://api.anthropic.com",dialect:"anthropic",key:true,keyenv:"ANTHROPIC_API_KEY",models:["claude-opus-4","claude-sonnet-4","claude-haiku-4"]},
  moonshot:{label:"Moonshot · Kimi",base:"https://api.moonshot.ai/v1",dialect:"openai",key:true,keyenv:"MOONSHOT_API_KEY",models:["kimi-k2","moonshot-v1-128k"]},
  openrouter:{label:"OpenRouter",base:"https://openrouter.ai/api/v1",dialect:"openai",key:true,keyenv:"OPENROUTER_API_KEY",models:["anthropic/claude-sonnet-4","openai/gpt-4o","deepseek/deepseek-chat"]},
  deepseek:{label:"DeepSeek",base:"https://api.deepseek.com/v1",dialect:"openai",key:true,keyenv:"DEEPSEEK_API_KEY",models:["deepseek-chat","deepseek-reasoner"]},
  groq:{label:"Groq",base:"https://api.groq.com/openai/v1",dialect:"openai",key:true,keyenv:"GROQ_API_KEY",models:["llama-3.3-70b-versatile"]},
  together:{label:"Together",base:"https://api.together.xyz/v1",dialect:"openai",key:true,keyenv:"TOGETHER_API_KEY",models:["meta-llama/Llama-3.3-70B-Instruct-Turbo"]},
  custom:{label:"Custom · any OpenAI-compatible",base:"",dialect:"generic",key:true,keyenv:"LLM_API_KEY",models:[]},
};
function initProviders(){const sel=document.getElementById('provider2');
  for(const[k,p]of Object.entries(PROVIDERS)){const o=document.createElement('option');o.value=k;o.textContent=p.label;sel.appendChild(o);}}
function onProvider(){const p=PROVIDERS[document.getElementById('provider2').value];
  document.getElementById('llm').value=p.base;
  document.getElementById('prov').value=p.dialect;
  document.getElementById('keyfld').style.display=p.key?'block':'none';
  document.getElementById('keyenv').textContent=p.key
    ? `· stored as a secret (${p.keyenv}), never written to agent.yaml or exposed in the browser` : '';
  const dl=document.getElementById('mdlopts');dl.innerHTML='';
  p.models.forEach(m=>{const o=document.createElement('option');o.value=m;dl.appendChild(o);});
  document.getElementById('mdl').value=p.models[0]||'';
  const r=document.getElementById('testres');r.textContent='';r.className='tres';}

// ── live wiring to /studio/* ─────────────────────────────────────────────────────
// api() posts JSON and attaches the CSRF token (required on mutations when auth is on); a 401 means
// the session lapsed → re-show the login overlay.
async function api(path,body){const h={'Content-Type':'application/json'};if(window.__CSRF)h['X-CSRF-Token']=window.__CSRF;
  const r=await fetch(path,{method:'POST',headers:h,body:JSON.stringify(body)});
  if(r.status===401||r.status===403){showLogin();} return r.json();}
// ── auth gate (only bites when the appliance has STUDIO_AUTH_PASSWORD set) ──
function showLogin(){document.getElementById('loginOverlay').hidden=false;}
async function doLogin(){
  const u=document.getElementById('login_user').value,p=document.getElementById('login_pass').value;
  const err=document.getElementById('login_err');err.textContent='';
  try{const r=await (await fetch('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({user:u,password:p})})).json();
    if(r.ok){window.__CSRF=r.csrf;document.getElementById('loginOverlay').hidden=true;document.getElementById('login_pass').value='';
      if(!window.__INIT)initApp();}                          // re-auth mid-edit: keep the draft, don't re-init
    else{err.textContent='✗ '+(r.error||'login failed');}
  }catch(e){err.textContent='✗ could not reach the studio';}}
function providerKeyEnv(){return PROVIDERS[document.getElementById('provider2').value]?.keyenv||'LLM_API_KEY';}
function llmSpec(){const dialect=document.getElementById('prov').value;
  return {provider:dialect==='anthropic'?'anthropic':'openai',
    base_url:document.getElementById('llm').value.trim(),
    model:document.getElementById('mdl').value.trim(),
    api_key:document.getElementById('key').value.trim(),
    family:dialect, api_key_env:providerKeyEnv()};}
async function fetchModels(){const spec=llmSpec();const r=document.getElementById('testres');
  if(spec.provider!=='anthropic'&&!spec.base_url){r.className='tres err';r.textContent='✗ no endpoint set';return;}
  r.className='tres wait';r.textContent='fetching models…';
  const res=await api('/studio/list-models',{provider:spec.provider,base_url:spec.base_url,api_key:spec.api_key});
  if(res.ok){const dl=document.getElementById('mdlopts');dl.innerHTML='';
    res.models.forEach(m=>{const o=document.createElement('option');o.value=m;dl.appendChild(o);});
    r.className='tres ok';r.textContent=`✓ ${res.models.length} model${res.models.length===1?'':'s'} available`;}
  else{r.className='tres err';r.textContent='✗ '+(res.error||'could not list models');}}
async function testLLM(){const spec=llmSpec();const r=document.getElementById('testres');
  if(spec.provider!=='anthropic'&&!spec.base_url){r.className='tres err';r.textContent='✗ no endpoint set';return;}
  if(!spec.model){r.className='tres err';r.textContent='✗ pick a model';return;}
  r.className='tres wait';r.textContent='testing (server-side)…';
  const res=await api('/studio/test-llm',spec);
  // textContent (not innerHTML): res.model/res.sample come from the REMOTE provider — a hostile
  // endpoint could return '<img onerror=…>'. Escaping here prevents HTML/script injection.
  if(res.ok){r.className='tres ok';r.textContent=`✓ reachable · ${res.model} replied${res.sample?' “'+res.sample+'”':''} · ${res.latency_ms}ms`;}
  else{r.className='tres err';r.textContent=`✗ ${res.error||'unreachable'}`;}}
function embeddingSpec(){if(document.getElementById('emb').value!=='remote')return null;
  return {provider:'openai',base_url:document.getElementById('emburl').value.trim(),
    model:document.getElementById('embmdl').value.trim()||'all-MiniLM-L6-v2',
    api_key:document.getElementById('embkey').value.trim(),
    dimensions:parseInt(document.getElementById('embdim').value,10)||384};}
// ── tools (Step 04): collect webhook / MCP / A2A entries into the actors config ──
let TOOLS=[], _toolType='webhook';
function _cap(s){return s.charAt(0).toUpperCase()+s.slice(1);}
function setToolType(t){_toolType=t;['webhook','mcp','a2a'].forEach(x=>{
  document.getElementById('tf'+_cap(x)).hidden=x!==t;
  document.getElementById('tt'+_cap(x)).setAttribute('aria-pressed',x===t);});}
function onMcpTransport(){const t=document.getElementById('mcp_transport').value;
  document.getElementById('mcp_url_f').hidden=t!=='http';document.getElementById('mcp_cmd_f').hidden=t!=='stdio';}
function _v(id){return document.getElementById(id).value.trim();}
function _clear(ids){ids.forEach(i=>document.getElementById(i).value='');}
function addTool(){
  let e=null;
  if(_toolType==='webhook'){const n=_v('wh_name'),u=_v('wh_url');if(!n||!u)return;
    e={_type:'webhook',name:n,url:u,method:_v('wh_method')};
    // comma-separated param names → a JSON-Schema the LLM sees (else the tool takes no args)
    const ps=_v('wh_params').split(',').map(s=>s.trim()).filter(Boolean);
    if(ps.length)e.parameters={type:'object',properties:Object.fromEntries(ps.map(p=>[p,{type:'string'}]))};
    _clear(['wh_name','wh_url','wh_params']);}
  else if(_toolType==='mcp'){const n=_v('mcp_name'),tr=_v('mcp_transport');if(!n)return;
    if(tr==='http'){const u=_v('mcp_url');if(!u)return;e={_type:'mcp',name:n,transport:'http',url:u};}
    else{const c=_v('mcp_cmd');if(!c)return;const p=c.split(/\s+/);e={_type:'mcp',name:n,transport:'stdio',command:p[0],args:p.slice(1)};}
    _clear(['mcp_name','mcp_url','mcp_cmd']);}
  else{const n=_v('a2a_name'),u=_v('a2a_url');if(!n||!u)return;e={_type:'a2a',name:n,card_url:u};_clear(['a2a_name','a2a_url']);}
  TOOLS.push(e);renderToolChips();}
function removeTool(i){TOOLS.splice(i,1);renderToolChips();}
function renderToolChips(){document.getElementById('toolchips').innerHTML=TOOLS.map((t,i)=>
  `<button class="pick" onclick="removeTool(${i})" title="click to remove"><div class="nm">${t.name}</div>`+
  `<div class="bl">${t._type}${t.method?' · '+t.method:''}${t.url?' · '+t.url:''}${t.command?' · '+t.command+' '+(t.args||[]).join(' '):''}${t.card_url?' · '+t.card_url:''}</div>`+
  `<div class="cnt">remove ✕</div></button>`).join('');}
function collectActors(){const a={};
  const w=TOOLS.filter(t=>t._type==='webhook').map(({_type,_seeded,...r})=>r);
  const m=TOOLS.filter(t=>t._type==='mcp').map(({_type,_seeded,...r})=>r);
  const g=TOOLS.filter(t=>t._type==='a2a').map(({_type,_seeded,...r})=>r);
  if(w.length)a.webhooks=w;if(m.length)a.mcp=m;if(g.length)a.a2a=g;
  return Object.keys(a).length?a:null;}
// ── hive membership (Step 05): solo (no block) or a shared_world member with a graph role ──
function onHiveMode(){document.getElementById('hive_role_f').hidden=
  document.getElementById('hive_mode').value!=='shared_world';}
function collectHive(){if(document.getElementById('hive_mode').value!=='shared_world')return null;
  return {mode:'shared_world',graph_role:document.getElementById('hive_role').value};}

async function create(){const aid=document.getElementById('aid').value.trim();const t=document.getElementById('toast');
  const flash=(m)=>{t.textContent=m;t.classList.add('on');setTimeout(()=>t.classList.remove('on'),4200);};
  if(!aid){flash('✗ agent needs an id');return;}
  const persona={agent_id:aid,
    objectives:document.getElementById('obj').value.split('\n').map(s=>s.trim()).filter(Boolean),
    world_entities:document.getElementById('ent').value.trim()};
  const body={agent_id:aid,enabled:[...enabled],persona,llm:llmSpec(),embedding:embeddingSpec(),
    outward_actions:'explore',autonomy:{level:document.getElementById('autonomy').value}};
  const actors=collectActors(); if(actors)body.actors=actors;
  const hive=collectHive(); if(hive)body.hive=hive;
  t.textContent='creating '+aid+'…';t.classList.add('on');
  const res=await api('/studio/create',body);
  if(res.ok){const dep=res.auto_enabled?.length?` (+${res.auto_enabled.length} deps)`:'';
    flash(`✓ ${aid} created · ${res.written.length} files${dep}${res.started?' · started':''}`);}
  else flash('✗ '+(res.error||'create failed'));}

// ── boot: gate on auth (if enabled), then fetch the live catalog + render ──
async function initApp(){
  try{
    const data=await (await fetch('/studio/catalog')).json();
    CATALOG={caps:data.capabilities.map(c=>({f:c.flag,g:c.group,s:c.summary,r:c.requires||[],sub:c.substrate||''})),
             presets:data.presets};
    CATALOG.caps.forEach(c=>CAPS[c.f]=c);
    if(Array.isArray(data.groups)&&data.groups.length)GROUP_ORDER=data.groups;
  }catch(e){
    document.getElementById('env').textContent='could not load /studio/catalog — is the studio backend running?';
    return;
  }
  setToolType('webhook');initProviders();onProvider();     // init tool form + provider before a template seeds into them
  renderTemplates();applyTemplate('researcher');           // applyTemplate now also sets the preset/autonomy/tools/hive
  setView('basic');onHiveMode();
  window.__INIT=true;                                        // one-time init done (guards re-auth re-runs)
}
(async function boot(){
  try{const s=await (await fetch('/auth/status')).json();
    if(s.enabled){ if(!s.authenticated){showLogin();return;} window.__CSRF=s.csrf; }  // restore CSRF on reload
  }catch(e){}
  initApp();
})();
</script>"""


def build() -> str:
    text = MOCKUP.read_text()
    head_body = text[: text.index("<script>")].rstrip()   # drop the mockup's simulated <script>
    style_end = head_body.index("</style>") + len("</style>")
    doc_head, doc_body = head_body[:style_end], head_body[style_end:]
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
        + doc_head + "\n</head>\n<body>\n" + doc_body.strip() + "\n" + SCRIPT + "\n</body>\n</html>\n"
    )


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    html = build()
    OUT.write_text(html)
    print(f"wrote {OUT} ({len(html)} bytes)")
