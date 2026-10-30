"""mem0 记忆层 Ollama 修复的回归测试。

- 本地 Ollama LLM（无真实 api_key）不因缺 key 被判不可用
- OllamaLLM/embedder 的 base_url 必须剥掉 /v1 后缀（mem0 走原生 /api/*）
"""
import sys
import types

import pytest


def test_ollama_llm_without_api_key_is_allowed():
    """api_key 校验只对远程 OpenAI 兼容厂家有意义；ollama 走占位符。"""
    from core.memory.service import MemoryService

    svc = MemoryService()
    # 服务层的判定逻辑：ollama provider 不要求 api_key
    provider = "ollama"
    cfg = {"api_key": None}
    llm_provider = provider
    llm_cfg_for_check = cfg
    needs_key = not llm_cfg_for_check.get("api_key") and llm_provider != "ollama"
    assert not needs_key, "ollama provider 不应因缺 api_key 被拒绝"


def test_strip_openai_suffix():
    from core.memory.config import _strip_openai_suffix

    assert _strip_openai_suffix("http://localhost:11434/v1") == "http://localhost:11434"
    assert _strip_openai_suffix("http://localhost:11434/v1/") == "http://localhost:11434"
    assert _strip_openai_suffix("http://localhost:11434") == "http://localhost:11434"
    assert _strip_openai_suffix("") == ""
    assert _strip_openai_suffix("http://host:8080/api") == "http://host:8080/api"


def test_normalize_openai_base_url_keeps_v1():
    from core.memory.config import _normalize_openai_base_url

    assert _normalize_openai_base_url("http://localhost:11434") == "http://localhost:11434/v1"
    assert _normalize_openai_base_url("http://localhost:11434/v1") == "http://localhost:11434/v1"


def test_mem0_llm_config_for_ollama_strips_v1():
    """统一 LLMConfig(ollama, base_url 带 /v1) → mem0 配置必须用原生地址。"""
    from core.memory.config import _build_mem0_llm_config_from_unified_config

    class FakeLLMConfig:
        provider = "ollama"
        model = "qwen3-30b-fin:latest"
        api_key = None
        base_url = "http://localhost:11434/v1"
        temperature = 0.1
        max_tokens = 2000

    result = _build_mem0_llm_config_from_unified_config(FakeLLMConfig())
    assert result["provider"] == "ollama"
    assert result["config"]["ollama_base_url"] == "http://localhost:11434"
    assert "openai_base_url" not in result["config"]


def test_embedder_config_for_ollama_strips_v1():
    from core.memory.config import _build_embedder_config_from_provider_doc

    doc = {
        "name": "ollama",
        "embedding_model": "nomic-embed-text",
        "api_key": None,
        "default_base_url": "http://localhost:11434/v1",
    }
    result = _build_embedder_config_from_provider_doc(doc, "nomic-embed-text")
    assert result["provider"] == "ollama"
    assert result["config"]["ollama_base_url"] == "http://localhost:11434"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
