import os
import pytest
from super_agent.config import Settings, validate_settings


def test_settings_defaults(monkeypatch):
    """测试默认值时隔离 .env 文件的影响"""
    # 清除 .env.dev 注入的环境变量，验证代码默认值
    for key in list(os.environ):
        if key.startswith("SA_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SA_ENV", "dev")
    s = Settings()
    assert s.env == "dev"
    assert s.llm.default_model == "gpt-4o"
    assert s.vector_store.provider == "chroma"
    assert s.redis.short_memory_ttl == 3600
    assert s.mysql.database == "super_agent"


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("SA_ENV", "prod")
    monkeypatch.setenv("SA_LLM_ONEAPI_API_KEY", "sk-test")
    monkeypatch.setenv("SA_MYSQL_PASSWORD", "secret")
    monkeypatch.setenv("SA_REDIS_PASSWORD", "redis-secret")
    monkeypatch.setenv("SA_VECTOR_PROVIDER", "milvus")
    s = Settings()
    assert s.env == "prod"
    assert s.llm.oneapi_api_key == "sk-test"
    assert s.mysql.password == "secret"
    assert s.vector_store.provider == "milvus"


def test_mysql_dsn():
    s = Settings()
    s.mysql.password = "pw"
    assert "mysql+asyncmy" in s.mysql.dsn
    assert ":pw@" in s.mysql.dsn


def test_validate_prod_missing_key(monkeypatch):
    monkeypatch.setenv("SA_ENV", "prod")
    monkeypatch.setenv("SA_LLM_ONEAPI_API_KEY", "")
    s = Settings()
    with pytest.raises(Exception, match="ONEAPI_API_KEY"):
        validate_settings(s)


def test_validate_prod_cors_wildcard(monkeypatch):
    monkeypatch.setenv("SA_ENV", "prod")
    monkeypatch.setenv("SA_LLM_ONEAPI_API_KEY", "sk-x")
    monkeypatch.setenv("SA_MYSQL_PASSWORD", "x")
    monkeypatch.setenv("SA_REDIS_PASSWORD", "x")
    s = Settings()
    with pytest.raises(Exception, match="CORS"):
        validate_settings(s)


def test_ocr_defaults(monkeypatch):
    for key in list(os.environ):
        if key.startswith("SA_OCR_"):
            monkeypatch.delenv(key, raising=False)
    s = Settings()
    assert s.ocr.enabled is True
    assert s.ocr.use_gpu is False
    assert s.ocr.lang == "ch"
    assert s.ocr.page_dpi == 200
    assert s.ocr.text_threshold == 0.1


def test_ocr_from_env(monkeypatch):
    monkeypatch.setenv("SA_OCR_USE_GPU", "true")
    monkeypatch.setenv("SA_OCR_LANG", "en")
    monkeypatch.setenv("SA_OCR_PAGE_DPI", "300")
    s = Settings()
    assert s.ocr.use_gpu is True
    assert s.ocr.lang == "en"
    assert s.ocr.page_dpi == 300
