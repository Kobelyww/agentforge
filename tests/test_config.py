"""Config loading: env interpolation, defaults, provider construction."""


from agentforge.config import ProviderSpec, _interpolate, load_settings


def test_interpolate_env(monkeypatch):
    monkeypatch.setenv("TEST_KEY_XYZ", "abc123")
    raw = {"a": "${TEST_KEY_XYZ}", "b": "${MISSING_VAR:fallback}", "nested": {"c": "${TEST_KEY_XYZ}"}}
    out = _interpolate(raw)
    assert out["a"] == "abc123"
    assert out["b"] == "fallback"
    assert out["nested"]["c"] == "abc123"


def test_load_settings_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for var in ("OPENAI_API_KEY", "GLM_API_KEY", "ANTHROPIC_API_KEY", "MODELARTS_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    s = load_settings()
    assert s.server.port == 8000
    assert s.providers[0].type == "mock"
    assert s.default_model == "mock/default"


def test_yaml_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
server:
  port: 9999
default_model: glm/glm-4-plus
providers:
  - name: glm
    type: openai
    base_url: https://open.bigmodel.cn/api/paas/v4
    api_key: ${GLM_TEST_KEY:stub-key}
    model: glm-4-plus
  - name: mock
    type: mock
    model: default
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GLM_TEST_KEY", raising=False)
    s = load_settings()
    assert s.server.port == 9999
    assert s.default_model == "glm/glm-4-plus"
    assert s.providers[0].api_key == "stub-key"  # default applied


def test_provider_registry_skips_keyless(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from agentforge.llm.registry import ProviderRegistry

    specs = [
        ProviderSpec(name="glm", type="openai", api_key="", model="glm-4-plus"),
        ProviderSpec(name="mock", type="mock", model="default"),
    ]
    registry = ProviderRegistry(specs, "mock/default")
    assert registry.names() == ["mock"]
    assert registry.resolve() == ("mock", "default")


def test_provider_registry_parse_model(tmp_path):
    from agentforge.llm.registry import ProviderRegistry

    registry = ProviderRegistry(
        [ProviderSpec(name="mock", type="mock", model="default")], "mock/default"
    )
    assert registry.resolve("mock/default") == ("mock", "default")
    assert registry.resolve("unknown/model") == ("mock", "default")


def test_data_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTFORGE_DATA_DIR", str(tmp_path / "custom"))
    s = load_settings()
    assert s.data_dir == tmp_path / "custom"
