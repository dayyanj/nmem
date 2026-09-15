"""Project the design mockup (studio-wizard.html) into the SERVED wizard SPA
(src/nmem/agent_core/studio_ui/index.html).

Anti-drift, by the same logic as the capability catalog: the mockup is the single source
of truth for the wizard's markup + style; this script wraps it as a standalone HTML document
and swaps the mockup's *simulated* <script> for one wired to the live /studio/* endpoints
(catalog fetched, Test/fetch-models/Create posted). Re-run after editing the mockup:

    python _design/mockups/build_studio_spa.py

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
const EDIT = location.pathname.replace(/\/+$/,'') === '/edit';   // served at /edit ⇒ reconfigure mode
let GROUP_ORDER = ["drives","concerns","goals","learning","metacognition","comms","recall","graph","prediction","obligations","meta","chat","perception"];
const GROUP_LABEL = {drives:"Drives", concerns:"Concerns", goals:"Goals", learning:"Experiential learning",
  metacognition:"Metacognition", comms:"Surprise to communication", recall:"Recall", graph:"World-model graph",
  prediction:"Prediction & hypotheses", obligations:"Obligations", meta:"Meta / self-improvement",
  chat:"Conversation & identity", perception:"Perception (embodied)"};
// value-flag picks (flag → chosen value); mirrors config_writer's `values` arg. Only value-flags
// (c.va non-empty) live here; a boolean flag is just present/absent in `enabled`.
const VALUES = {};
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
    // an EXAMPLE that pre-fills the tool form (so the actor step isn't empty) — it is NOT added as a
    // real tool until the user swaps in a real endpoint and clicks Add, so no placeholder ships.
    tool:{name:"get_weather", method:"GET", url:"https://api.example.com/weather?city={city}", params:"city"}},
  analyst:{nm:"Watcher", aid:"watcher", preset:"reflective",
    obj:"Continuously watch your domain and notice what changed.\nSurface what matters, and why, before it's asked for.\nSeek out the unknown rather than guess at it.",
    ent:"the domain you watch, its sources and signals"},
  teammate:{nm:"Teammate", aid:"teammate", preset:"full_cognition", hive:"shared_world",
    obj:"Contribute to a shared understanding alongside other agents.\nDo your part and keep your own goals; build on what the others learn.",
    ent:"the shared domain, the teammates you work alongside"},
  companion:{nm:"Companion", aid:"companion", preset:"reflective",
    obj:"Be a thoughtful companion who remembers our conversations.\nListen first, and pick up where we left off.\nNotice how things are going for me over time.",
    ent:"our conversations, what matters to you, how things change over time"},
  everything:{nm:"Everything", aid:"everything", preset:"everything", autonomy:"read_only",
    obj:"Exercise the full stack: learn a domain from primary sources, form drives and goals, predict and reflect, and act with the tools you're given.\nSeek and verify rather than guess; remember what works and why it failed.",
    ent:"your domain, its sources, the tools and people you work with",
    tool:{name:"get_weather", method:"GET", url:"https://api.example.com/weather?city={city}", params:"city"}},
};

// ── contextual help (click a ⓘ to open a modal — the non-obvious concepts explained in place) ──
const HELP = {
  capabilities:{title:"Capabilities: presets vs. Advanced",html:`
    <p><b>Presets</b> (the Basic tab) are ready-made, dependency-complete bundles — one click gives a coherent mind, from a plain remembering assistant up to the full cognitive suite.</p>
    <p>Switch to the <b>Advanced</b> tab to see every capability as an individual toggle, grouped by area, and turn extras on or off. The wizard auto-pulls in whatever a capability depends on, so you can't build a broken config, and the live <code>capabilities.env</code> preview updates as you go.</p>
    <p>Anything you enable that needs an optional service (writing-style identity, the perception sandbox…) is still created — the agent's dashboard then shows a clear banner telling you exactly what to start.</p>`},
  autonomy:{title:"Autonomy — what it may do without asking",html:`
    <p><b>read-only</b> (default, safe): the agent may only observe — call read-only tools, never change anything. A fresh agent can't act until you raise this.</p>
    <p><b>tiered</b>: read-only is always allowed, plus an allowlist of actions you control; everything else is blocked.</p>
    <p><b>full</b>: the agent may run any tool you configured here. Use deliberately.</p>
    <p>Autonomy governs <i>acting</i> — separate from which thinking/memory <b>capabilities</b> you chose in Step 03.</p>`},
  tools:{title:"Giving the agent hands",html:`
    <p><b>Webhook</b> — expose any HTTP endpoint of yours as a tool: a name, a URL with <code>{placeholders}</code>, and the parameter names the agent may fill. Its LLM calls it like a function.</p>
    <p><b>MCP server</b> — connect a Model Context Protocol tool-server (Streamable HTTP, or a local stdio process); all of its tools become available at once.</p>
    <p><b>Agent (A2A)</b> — point at <i>another agent's</i> "agent card" URL and your agent can <b>delegate a task</b> to it, as if that whole agent were one tool. It's the loose "ask another agent" link — contrast the tight shared-graph coupling of a hive in Step 05.</p>
    <p>Every call is governed by the <b>autonomy</b> level above and recorded so the agent learns what works.</p>`},
  hive:{title:"Solo, or part of a hive",html:`
    <p><b>Solo</b> (default): the agent owns its own private world-model (symbol graph).</p>
    <p><b>Hive member</b>: several agents share ONE world-model and build on each other's knowledge, while each keeps its own goals, drives and pursuit (owner-scoped). Exactly one member is the <b>keeper</b> that runs the heavy graph-global maintenance (clustering, dreamstate).</p>
    <p>To form a hive, point every member's <code>NMEM_AGENT_DB</code> at the same database. This is the <i>tight</i> coupling; for one agent to merely hand work to another, use an <b>A2A</b> tool in Step 04 instead.</p>`},
};
function openHelp(key){
  const h=HELP[key]; if(!h) return;
  let ov=document.getElementById('helpov');
  if(!ov){ov=document.createElement('div');ov.id='helpov';ov.className='modalov';
    ov.onclick=e=>{ if(e.target===ov) closeHelp(); };
    ov.innerHTML='<div class="modal" role="dialog" aria-modal="true"><button class="x" onclick="closeHelp()" aria-label="Close">×</button><div id="helpbody"></div></div>';
    document.body.appendChild(ov);
    document.addEventListener('keydown',e=>{ if(e.key==='Escape') closeHelp(); });}
  document.getElementById('helpbody').innerHTML=`<h3>${h.title}</h3>${h.html}`;
  ov.classList.add('on');
}
function closeHelp(){const ov=document.getElementById('helpov'); if(ov) ov.classList.remove('on');}

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
function selectPreset(name){preset=name;const acc=new Set();(CATALOG.presets[name].flags||[]).forEach(f=>addWithDeps(f,acc));enabled=acc;render();
  const hint=document.getElementById('presethint');
  if(hint){const lbl=CATALOG.presets[name]?.label||name;
    hint.innerHTML=`<b>${lbl}</b> selected — ${acc.size} capabilities. Want more or fewer? Switch to the `
      +`<a onclick="setView('advanced')">Advanced</a> tab to toggle any capability individually.`;
    hint.hidden=false;}
}
// value-flags: the default pick is the last (most-capable) choice; config_writer applies the same default.
function defaultValue(f){const va=CAPS[f]?.va||[];return va.length?va[va.length-1]:'';}
function syncValues(){for(const f of Object.keys(VALUES))if(!enabled.has(f))delete VALUES[f];
  CATALOG.caps.forEach(c=>{if((c.va||[]).length&&enabled.has(c.f)&&!VALUES[c.f])VALUES[c.f]=defaultValue(c.f);});}
function setValue(f,v){VALUES[f]=v;renderEnv();}
function render(){syncValues();renderPresets();renderGroups();renderEnv();syncPresets();document.getElementById('ec').textContent=enabled.size+" on";}
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
      const short=c.f.replace(/^NMEM_(SYM_)?/,'').replace(/^CHAT_/,'CHAT · ');
      const dep=ison&&(c.r||[]).length?`<span class="depmark" title="pulls in ${c.r.join(', ')}">+dep</span>`:'';
      const sub=c.sub?`<div class="sub"><b>needs:</b> ${c.sub}</div>`:'';
      // value-flag (e.g. POSTMORTEM_MODE): once on, a picker chooses which value is written (click
      // doesn't toggle the pill — stopPropagation). config_writer emits the chosen value, not `true`.
      let valsel='';
      if((c.va||[]).length&&ison){valsel=`<select class="valsel" onclick="event.stopPropagation()" `+
        `onkeydown="event.stopPropagation()" onchange="setValue('${c.f}',this.value)" `+
        `style="margin-top:8px;font-family:var(--mono);font-size:12px;padding:2px 6px;border-radius:6px">`+
        (c.va).map(v=>`<option value="${v}"${VALUES[c.f]===v?' selected':''}>${v}</option>`).join('')+`</select>`;}
      p.innerHTML=`${dep}<div class="fl">${short}</div><div class="ds">${c.s}</div>${valsel}${sub}`;pills.appendChild(p);});
    wrap.appendChild(pills);host.appendChild(wrap);}}
// env preview mirrors config_writer.render_capabilities_env exactly: booleans 'true', value-flags
// carry their chosen value (DRIVES_OUTWARD_ACTIONS=explore, POSTMORTEM_MODE=canary|active), and
// RECALL_DRIVE auto-writes RECALL_AGENT_ID. Remedy-goals coerces POSTMORTEM_MODE=active (as the writer does).
function envValue(f){
  if(f==='NMEM_SYM_DRIVES_OUTWARD_ACTIONS')return 'explore';
  if(f==='NMEM_SYM_POSTMORTEM_MODE'&&enabled.has('NMEM_SYM_POSTMORTEM_REMEDY_GOALS'))return 'active';
  const va=CAPS[f]?.va||[];
  if(va.length)return VALUES[f]||va[va.length-1];
  return 'true';}
function renderEnv(){const on=[...enabled].sort();
  let html=`<span class="c"># capabilities.env, generated by nmem.agent_core.config_writer\n# dependency-complete · booleans only · defaults omitted\n</span>`;
  if(!on.length)html+=`<span class="c"># (nothing on yet: a blank mind)</span>`;
  let lastG=null;
  on.forEach(f=>{const g=CAPS[f]?.g;if(g!==lastG){html+=`<span class="c">\n# ${GROUP_LABEL[g]||g}\n</span>`;lastG=g;}
    html+=`<span class="k">${f}</span>=<span class="v">${envValue(f)}</span>\n`;});
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
    companion:["Companion","A reflective companion that remembers your context over time."],
    everything:["Everything (full suite)","Every capability enabled — the whole cognitive stack. A showcase; some capabilities need an optional profile (the dashboard will say which)."]};
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
  // Prefill the tool FORM with the template's example (inviting + educational) — never commit a
  // placeholder as a real tool; the user edits the endpoint and clicks Add to actually include it.
  const ex=t.tool||{}, wn=document.getElementById('wh_name'), wu=document.getElementById('wh_url'),
        wp=document.getElementById('wh_params'), wm=document.getElementById('wh_method');
  if(wn){wn.value=ex.name||'';wu.value=ex.url||'';wp.value=ex.params||'';if(wm&&ex.method)wm.value=ex.method;}
  if(t.tool)setToolType('webhook');
  const hm=document.getElementById('hive_mode'); if(hm){hm.value=t.hive||'isolated';onHiveMode();}
  if(CATALOG)renderEnv();}
// ── edit mode: rehydrate the wizard from the running agent's config, then Save → reconfigure ──
async function loadCurrent(){
  let spec=null;
  try{const j=await (await fetch('/studio/current')).json(); if(j.ok) spec=j.spec;}catch(e){}
  if(!spec){document.getElementById('env').textContent='could not load the current agent config';return;}
  applyCurrent(spec);
}
function setupEditMode(aid){
  const btn=document.getElementById('createbtn'); if(btn) btn.textContent='Save changes →';
  const aidEl=document.getElementById('aid'); if(aidEl){aidEl.readOnly=true;aidEl.style.opacity='.6';}
  const nmEl=document.getElementById('nm'); if(nmEl) nmEl.readOnly=true;
  const tmpl=document.getElementById('tmpl'); if(tmpl) tmpl.style.display='none';
  if(!document.getElementById('editbanner')){
    const b=document.createElement('div');b.id='editbanner';
    b.style.cssText='margin:0 0 16px;padding:11px 14px;border-radius:10px;border:1px solid var(--accent);'
      +'background:var(--accent-soft,var(--surface-2));color:var(--ink);font-size:14px;line-height:1.5';
    b.innerHTML=`Editing <b style="font-family:var(--mono)">${aid}</b> — change anything below and Save. Its `
      +`<b>memory is preserved</b>; the agent restarts into the new settings. The id can't change.`;
    const host=document.querySelector('main>div')||document.querySelector('main');
    if(host) host.insertBefore(b, host.firstChild);
  }
}
function applyCurrent(spec){
  const aid=spec.agent_id||(spec.persona&&spec.persona.agent_id)||'agent';
  document.getElementById('nm').value=aid; document.getElementById('aid').value=aid;
  const p=spec.persona||{};
  document.getElementById('obj').value=(p.objectives||[]).join('\n');
  document.getElementById('ent').value=p.world_entities||'';
  const fam=(spec.llm&&spec.llm.family)||'generic';
  document.getElementById('provider2').value=(fam==='anthropic')?'anthropic':(fam==='openai'?'openai':'custom');
  onProvider();                                          // set provider defaults, then override with the real values
  if(spec.llm){document.getElementById('llm').value=spec.llm.base_url||'';
    document.getElementById('prov').value=fam; document.getElementById('mdl').value=spec.llm.model||'';}
  const tr=document.getElementById('testres'); if(tr){tr.className='tres';tr.textContent='key kept — re-enter only to change it';}
  const emb=spec.embedding;
  if(emb&&emb.provider&&emb.provider!=='sentence-transformers'){
    document.getElementById('emb').value='remote'; onEmb();
    document.getElementById('emburl').value=emb.base_url||''; document.getElementById('embmdl').value=emb.model||'';
    if(emb.dimensions) document.getElementById('embdim').value=emb.dimensions;}
  enabled=new Set(spec.enabled||[]); Object.keys(VALUES).forEach(k=>delete VALUES[k]);
  Object.assign(VALUES, spec.values||{}); preset=null; render(); setView('advanced');
  if(spec.autonomy&&spec.autonomy.level) document.getElementById('autonomy').value=spec.autonomy.level;
  TOOLS=[]; const a=spec.actors||{};
  (a.webhooks||[]).forEach(w=>TOOLS.push(Object.assign({_type:'webhook'},w)));
  (a.mcp||[]).forEach(m=>TOOLS.push(Object.assign({_type:'mcp'},m)));
  (a.a2a||[]).forEach(g=>TOOLS.push(Object.assign({_type:'a2a'},g)));
  renderToolChips();
  const cu=spec.computer_use;
  if(cu&&cu.enabled){document.getElementById('cu_mode').value='on';onCuMode();
    if(cu.url)document.getElementById('cu_url').value=cu.url;
    if(cu.max_steps)document.getElementById('cu_steps').value=cu.max_steps;}
  const hv=spec.hive;
  if(hv&&hv.mode==='shared_world'){document.getElementById('hive_mode').value='shared_world';onHiveMode();
    if(hv.graph_role)document.getElementById('hive_role').value=hv.graph_role;}
  setupEditMode(aid);
}
function onName(){if(EDIT)return;document.getElementById('aid').value=document.getElementById('nm').value.trim().toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_|_$/g,'');if(CATALOG)renderEnv();}
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
  const w=TOOLS.filter(t=>t._type==='webhook').map(({_type,...r})=>r);
  const m=TOOLS.filter(t=>t._type==='mcp').map(({_type,...r})=>r);
  const g=TOOLS.filter(t=>t._type==='a2a').map(({_type,...r})=>r);
  if(w.length)a.webhooks=w;if(m.length)a.mcp=m;if(g.length)a.a2a=g;
  return Object.keys(a).length?a:null;}
// ── research sandbox (Step 04b): a computer_use block ⇒ the direct verifier-enforced research
// runner (§33.3). Mutually exclusive with selector tools — the appliance ignores actors: when a
// computer_use block is present. ──
function onCuMode(){document.getElementById('cu_fields').hidden=
  document.getElementById('cu_mode').value!=='on';}
function collectComputerUse(){if(document.getElementById('cu_mode').value!=='on')return null;
  const cu={enabled:true};const url=_v('cu_url');if(url)cu.url=url;
  const s=parseInt(_v('cu_steps'),10);if(!isNaN(s))cu.max_steps=s;return cu;}
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
    outward_actions:'explore',values:{...VALUES},autonomy:{level:document.getElementById('autonomy').value}};
  const cu=collectComputerUse();
  if(cu){body.computer_use=cu;}                 // research agent: the sandbox is its one actuator (§33.3)
  else{const actors=collectActors(); if(actors)body.actors=actors;}  // else selector tools (if any)
  const hive=collectHive(); if(hive)body.hive=hive;
  t.textContent=(EDIT?'saving ':'creating ')+aid+'…';t.classList.add('on');
  const res=await api(EDIT?'/studio/reconfigure':'/studio/create',body);
  if(res.ok){
    // Both create (started:true) and reconfigure (restarting:true) restart the container → hold the
    // "building…" interstitial and land back on the dashboard. A config-only create just confirms.
    if(EDIT||res.started||res.restarting) waitForAgent(aid);
    else{const dep=res.auto_enabled?.length?` (+${res.auto_enabled.length} deps)`:'';
      flash(`✓ ${aid} created · ${res.written.length} files${dep}`);}
  }
  else flash('✗ '+(res.error||(EDIT?'save failed':'create failed')));}

// ── post-Create interstitial: the container restarts into agent mode, so hold a friendly overlay
// and poll /health until the agent is up, then land on its dashboard. No "what just happened?". ──
function waitForAgent(aid){
  let ov=document.getElementById('bootov');
  if(!ov){ov=document.createElement('div');ov.id='bootov';
    ov.style.cssText='position:fixed;inset:0;z-index:2000;display:flex;align-items:center;justify-content:center;'
      +'background:var(--bg,#0b0d10);color:var(--ink,#e8eaed);padding:24px;text-align:center';
    ov.innerHTML='<div style="max-width:460px">'
      +'<div class="bootspin" style="width:38px;height:38px;margin:0 auto 18px;border:3px solid var(--line-2,#333);'
      +'border-top-color:var(--accent,#6ea8fe);border-radius:50%;animation:bootspin 0.9s linear infinite"></div>'
      +'<h2 style="margin:0 0 8px">'+(EDIT?'Applying changes to ':'Building ')+'<span style="font-family:var(--mono,monospace)">'+aid+'</span>…</h2>'
      +'<p id="bootmsg" style="margin:0;color:var(--muted,#9aa)">'
      +(EDIT?'Saving the new settings and restarting into them — your agent’s memory is kept. This takes ~15 seconds.'
            :'Provisioning its memory and waking it up. The appliance restarts into your agent — this takes ~15 seconds.')
      +'</p></div>';
    const st=document.createElement('style');st.textContent='@keyframes bootspin{to{transform:rotate(360deg)}}';
    document.head.appendChild(st);document.body.appendChild(ov);}
  const msg=document.getElementById('bootmsg');
  const t0=Date.now();
  const tick=async()=>{
    try{
      const r=await fetch('/health',{cache:'no-store'});
      if(r.ok){const h=await r.json(); if(h && h.agent_id){ location.href='/'; return; }}  // agent mode is up
    }catch(e){/* container mid-restart: keep waiting */}
    if(Date.now()-t0>25000 && msg) msg.textContent='Still waking up… (a first boot can take a little longer). '
      +'If this lingers, check `docker compose logs studio`.';
    if(Date.now()-t0>120000){ if(msg)msg.innerHTML='Taking longer than expected. '
      +'Check <code>docker compose logs studio</code>, then <a href="/" style="color:var(--accent,#6ea8fe)">reload</a>.'; return; }
    setTimeout(tick,2000);
  };
  setTimeout(tick,2500);   // give the SIGTERM/restart a moment before the first poll
}

// ── boot: gate on auth (if enabled), then fetch the live catalog + render ──
async function initApp(){
  try{
    const data=await (await fetch('/studio/catalog')).json();
    CATALOG={caps:data.capabilities.map(c=>({f:c.flag,g:c.group,s:c.summary,r:c.requires||[],sub:c.substrate||'',va:c.values||[]})),
             presets:data.presets};
    CATALOG.caps.forEach(c=>CAPS[c.f]=c);
    if(Array.isArray(data.groups)&&data.groups.length)GROUP_ORDER=data.groups;
  }catch(e){
    document.getElementById('env').textContent='could not load /studio/catalog — is the studio backend running?';
    return;
  }
  setToolType('webhook');initProviders();onProvider();     // init tool form + provider before a template seeds into them
  renderTemplates();
  if(EDIT){ await loadCurrent(); }                          // rehydrate from the running agent (reconfigure mode)
  else { applyTemplate('researcher'); setView('basic'); }   // fresh create: seed a starter template
  onHiveMode();
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
