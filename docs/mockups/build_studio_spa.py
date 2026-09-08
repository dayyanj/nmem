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
  researcher:{nm:"Scout", aid:"scout",
    obj:"Continuously build an accurate model of your domain from primary sources.\nNever let an unknown drive a guessed answer. Seek it out and verify.",
    ent:"your domain, its primary sources"},
  critic:{nm:"Michelle", aid:"michelle",
    obj:"Challenge the reasoning of your peers: surface the missed assumption and the unpriced failure mode.\nOffer a genuinely different view; agree plainly when it's warranted.\nApply human psychology where it changes the answer.\nSeek and verify knowledge rather than confabulate.",
    ent:"the peers you challenge, the domain you reason about"},
  support:{nm:"Ada", aid:"ada",
    obj:"Answer grounded in the product knowledge base; never invent capabilities.\nEscalate honestly when you can't verify something.\nBe concise and specific.",
    ent:"the product, its documentation, common user tasks"},
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
  const meta={researcher:["Researcher","Learns a domain from primary sources; verifies rather than guesses."],
    critic:["Diverse-prior critic","Challenges reasoning and offers a genuinely different view (michelle-style)."],
    support:["Support responder","Answers grounded in a knowledge base and escalates honestly."]};
  for(const[k,[nm,bl]]of Object.entries(meta)){const b=document.createElement('button');b.className='pick';b.dataset.t=k;
    b.setAttribute('aria-pressed',k==='researcher');b.onclick=()=>applyTemplate(k);
    b.innerHTML=`<div class="nm">${nm}</div><div class="bl">${bl}</div>`;host.appendChild(b);}}
function applyTemplate(k){const t=TEMPLATES[k];document.getElementById('nm').value=t.nm;document.getElementById('aid').value=t.aid;
  document.getElementById('obj').value=t.obj;document.getElementById('ent').value=t.ent;
  document.querySelectorAll('#tmpl .pick').forEach(b=>b.setAttribute('aria-pressed',b.dataset.t===k));
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
async function api(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});return r.json();}
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
  if(res.ok){r.className='tres ok';r.innerHTML=`✓ reachable · <b>${res.model}</b> replied${res.sample?' “'+res.sample+'”':''} · ${res.latency_ms}ms`;}
  else{r.className='tres err';r.textContent=`✗ ${res.error||'unreachable'}`;}}
function embeddingSpec(){if(document.getElementById('emb').value!=='remote')return null;
  return {provider:'openai',base_url:document.getElementById('emburl').value.trim(),
    model:document.getElementById('embmdl').value.trim()||'all-MiniLM-L6-v2',
    api_key:document.getElementById('embkey').value.trim(),
    dimensions:parseInt(document.getElementById('embdim').value,10)||384};}
async function create(){const aid=document.getElementById('aid').value.trim();const t=document.getElementById('toast');
  const flash=(m)=>{t.textContent=m;t.classList.add('on');setTimeout(()=>t.classList.remove('on'),4200);};
  if(!aid){flash('✗ agent needs an id');return;}
  const persona={agent_id:aid,
    objectives:document.getElementById('obj').value.split('\n').map(s=>s.trim()).filter(Boolean),
    world_entities:document.getElementById('ent').value.trim()};
  const body={agent_id:aid,enabled:[...enabled],persona,llm:llmSpec(),embedding:embeddingSpec(),outward_actions:'explore'};
  t.textContent='creating '+aid+'…';t.classList.add('on');
  const res=await api('/studio/create',body);
  if(res.ok){const dep=res.auto_enabled?.length?` (+${res.auto_enabled.length} deps)`:'';
    flash(`✓ ${aid} created · ${res.written.length} files${dep}${res.started?' · started':''}`);}
  else flash('✗ '+(res.error||'create failed'));}

// ── boot: fetch the live catalog, then render ──
(async function(){
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
  renderTemplates();applyTemplate('researcher');selectPreset('reflective');setView('basic');initProviders();onProvider();
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
