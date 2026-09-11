"""The config writer must produce a correct-by-construction capabilities.env + a
secret-free agent.yaml, and round-trip persona data."""
from nmem.agent_core.capabilities import check_env, enabled_flags, preset_flags
from nmem.agent_core.config_writer import (
    build_agent_files,
    render_agent_yaml,
    render_capabilities_env,
    split_secrets,
)
from nmem.agent_core.persona import Persona


def _parse_env(text: str) -> dict:
    env = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def test_env_is_dependency_complete_and_rule_compliant():
    # recall drive alone → writer must pull in drives+concerns+autonomy and set agent id
    text = render_capabilities_env({"NMEM_SYM_RECALL_DRIVE_ENABLED"}, agent_id="scout")
    env = _parse_env(text)
    # dependency-complete: the validator sees zero issues on what we wrote
    assert check_env(env) == []
    on = enabled_flags(env)
    assert {"NMEM_SYM_DRIVES_ENABLED", "NMEM_SYM_CONCERNS_ENABLED",
            "NMEM_AUTONOMY__ENABLED", "NMEM_SYM_RECALL_DRIVE_ENABLED"} <= on
    # value-constraint auto-written
    assert env["NMEM_SYM_RECALL_AGENT_ID"] == "scout"


def test_no_inline_comments_and_booleans_only():
    text = render_capabilities_env(preset_flags("full_cognition"), agent_id="ada")
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        assert "=" in line
        # the crash footgun: a value must never carry an inline comment
        assert "#" not in line, f"inline comment leaked: {line!r}"
        k, _, v = line.partition("=")
        if k.endswith("_ENABLED") or k in ("NMEM_AUTONOMY__PROACTIVE_RETRIEVE",):
            assert v in ("true", "false"), f"non-boolean toggle value: {line!r}"


def test_preset_env_validates():
    for name in ("memory", "reflective", "full_cognition"):
        env = _parse_env(render_capabilities_env(preset_flags(name), agent_id="x"))
        assert check_env(env) == [], f"preset {name} produced an invalid env"


def test_agent_yaml_excludes_secrets():
    y = render_agent_yaml(agent_id="scout",
                          llm={"provider": "anthropic", "base_url": "https://api.anthropic.com/v1",
                               "model": "claude", "api_key": "sk-SECRET", "api_key_env": "ANTHROPIC_API_KEY"})
    assert "sk-SECRET" not in y             # the key value is never written
    assert "scout-cognition" in y            # default domain
    assert "SCOUT_DB_DSN_ASYNC" in y         # env-key reference, not a literal DSN


def test_agent_yaml_configures_the_brain():
    import yaml
    doc = yaml.safe_load(render_agent_yaml(
        agent_id="scout",
        llm={"provider": "openai", "base_url": "http://vllm:8000/v1", "model": "gemma",
             "family": "gemma", "api_key": "sk-SECRET", "api_key_env": "SCOUT_LLM_TOKEN"}))
    brain = doc["backends"]["brain"]         # what AgentRuntime.build_backend reads
    assert brain["url"] == "http://vllm:8000/v1" and brain["model"] == "gemma"
    assert brain["family"] == "gemma" and brain["api_key_env"] == "SCOUT_LLM_TOKEN"
    assert "api_key" not in brain            # the NAME is written, never the key value


def test_split_secrets():
    s = split_secrets(agent_id="scout", llm_key="sk-123", llm_key_env="ANTHROPIC_API_KEY", embed_key="")
    assert s == {"ANTHROPIC_API_KEY": "sk-123"}
    assert split_secrets(agent_id="scout") == {}   # nothing to store


def test_agent_yaml_actors_and_autonomy():
    import yaml
    doc = yaml.safe_load(render_agent_yaml(
        agent_id="scout",
        llm={"provider": "openai", "base_url": "http://x/v1", "model": "m"},
        actors={"mcp": [{"name": "gh", "transport": "http", "url": "https://mcp.example/x"}],
                "webhooks": [{"name": "ping", "url": "http://x/ping"}]},
        autonomy={"level": "tiered", "allow": ["llm_tool_call", "gh_search"]}))
    assert doc["actors"]["mcp"][0]["url"] == "https://mcp.example/x"
    assert doc["autonomy"]["level"] == "tiered"
    # omitted when not provided (pure thinker default)
    plain = yaml.safe_load(render_agent_yaml(agent_id="x", llm={"provider": "openai", "model": "m"}))
    assert "actors" not in plain and "autonomy" not in plain


def test_agent_yaml_computer_use_block():
    import yaml
    doc = yaml.safe_load(render_agent_yaml(
        agent_id="scout",
        llm={"provider": "openai", "base_url": "http://x/v1", "model": "m"},
        pursuit={"enabled": True, "action_type": "pursue_knowledge"},
        computer_use={"enabled": True, "url": "http://sandbox:8080", "max_steps": 40}))
    assert doc["computer_use"]["url"] == "http://sandbox:8080"
    assert doc["computer_use"]["enabled"] is True
    # omitted when not provided (selector/thinker agent, no research sandbox)
    plain = yaml.safe_load(render_agent_yaml(agent_id="x", llm={"provider": "openai", "model": "m"}))
    assert "computer_use" not in plain
    # but an EXPLICIT empty block is preserved — the appliance reads presence (§33.3), so an empty
    # (disabled) research declaration must not silently degrade into a selector agent.
    empty = yaml.safe_load(render_agent_yaml(
        agent_id="x", llm={"provider": "openai", "model": "m"}, computer_use={}))
    assert "computer_use" in empty and empty["computer_use"] == {}


def test_build_agent_files_emits_research_computer_use_block():
    import yaml
    files = build_agent_files({
        "agent_id": "scout",
        "enabled": {"NMEM_SYM_GOALS_ENABLED"},
        "llm": {"provider": "openai", "base_url": "http://x/v1", "model": "m"},
        "pursuit": {"enabled": True, "action_type": "pursue_knowledge",
                    "tool_tag": "web research"},
        "computer_use": {"enabled": True, "url": "http://sandbox:8080"},
    })
    doc = yaml.safe_load(files["agent.yaml"])
    assert doc["computer_use"]["url"] == "http://sandbox:8080"
    assert doc["pursuit"]["action_type"] == "pursue_knowledge"


def test_persona_round_trip():
    p = Persona(agent_id="scout", objectives=[("learn", "Learn the domain.")],
                goal_priorities={"learn": 0.9},
                world_seed_topics=[("d", "Learn X.", 0.8)],
                baseline_kb={"who": ("Scout is an agent.", "self_fact")},
                capabilities="reason over text", capabilities_key="scout_caps",
                world_entities="the domain")
    p2 = Persona.from_dict(p.to_dict())
    assert p2 == p


def test_persona_from_lenient_dict():
    p = Persona.from_dict({"agent_id": "a", "objectives": ["Just do the thing."]})
    assert p.objectives[0][1] == "Just do the thing." and p.objectives[0][0] == "obj1"


def test_build_agent_files_end_to_end():
    files = build_agent_files({
        "agent_id": "scout",
        "enabled": {"NMEM_SYM_RECALL_DRIVE_ENABLED"},
        "persona": {"agent_id": "scout", "objectives": ["Learn."], "world_entities": "the domain"},
        "llm": {"provider": "openai", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o", "api_key": "sk-XYZ", "api_key_env": "OPENAI_API_KEY"},
    })
    assert check_env(_parse_env(files["capabilities.env"])) == []
    assert "sk-XYZ" not in files["agent.yaml"]
    assert files["secrets"] == {"OPENAI_API_KEY": "sk-XYZ"}
    assert "objectives" in files["persona.yaml"]


def test_goals_agent_boots_single_driver_lifecycle_end_state():
    """B-ii D2 end-state: an appliance agent WITH goals boots single-driver — capabilities.env sets
    NMEM_SYM_GOAL_LIFECYCLE_EXTERNAL=true (dreamstate skips it) AND agent.yaml carries
    goal_lifecycle.loop_enabled (the per-agent loop drives it). Pulled in via the dependency closure
    (DRIVES_CREATE_GOALS requires GOALS_ENABLED). A goals-LESS agent gets neither (both inert)."""
    import yaml

    goals = build_agent_files({
        "agent_id": "g", "enabled": {"NMEM_SYM_DRIVES_CREATE_GOALS"},   # requires GOALS_ENABLED → closure
        "llm": {"provider": "openai", "base_url": "http://x/v1", "model": "m"},
    })
    assert "NMEM_SYM_GOAL_LIFECYCLE_EXTERNAL=true" in goals["capabilities.env"]
    assert yaml.safe_load(goals["agent.yaml"])["goal_lifecycle"]["loop_enabled"] is True

    nogoals = build_agent_files({
        "agent_id": "n", "enabled": {"NMEM_SYM_SCHEMAS_ENABLED"},
        "llm": {"provider": "openai", "base_url": "http://x/v1", "model": "m"},
    })
    assert "GOAL_LIFECYCLE_EXTERNAL" not in nogoals["capabilities.env"]
    assert "goal_lifecycle" not in (yaml.safe_load(nogoals["agent.yaml"]) or {})
