from saturn.llm.anthropic_client import _build_params


def test_build_params_sets_model_and_messages():
    params = _build_params("system text", "user prompt", "claude-test")
    assert params["model"] == "claude-test"
    assert params["max_tokens"] > 0
    assert params["messages"] == [{"role": "user", "content": "user prompt"}]


def test_build_params_uses_prompt_caching_on_system():
    params = _build_params("system text", "user prompt", "claude-test")
    system_block = params["system"][0]
    assert system_block["text"] == "system text"
    assert system_block["cache_control"] == {"type": "ephemeral"}
