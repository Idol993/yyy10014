import os
import tempfile
import pytest

from src.config import Config


class TestConfig:
    def test_default_values(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        
        config = Config()
        
        assert config.openai_model == "gpt-4-turbo-preview"
        assert config.openai_temperature == 0.1
        assert config.github_head_branch == "main"
        assert config.enable_sandbox == True
        assert config.enable_security_scan == True
        assert config.enable_compatibility_check == True
        assert config.max_fix_attempts == 3
        assert config.rag_top_k == 5
    
    def test_custom_values(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_MODEL", "gpt-3.5-turbo")
        monkeypatch.setenv("OPENAI_TEMPERATURE", "0.7")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        monkeypatch.setenv("GITHUB_REPOSITORY", "user/repo")
        monkeypatch.setenv("GITHUB_HEAD_BRANCH", "develop")
        monkeypatch.setenv("ENABLE_SANDBOX", "false")
        monkeypatch.setenv("MAX_FIX_ATTEMPTS", "5")
        monkeypatch.setenv("RAG_TOP_K", "10")
        
        config = Config()
        
        assert config.openai_api_key == "test-key"
        assert config.openai_model == "gpt-3.5-turbo"
        assert config.openai_temperature == 0.7
        assert config.github_token == "ghp_test"
        assert config.github_repository == "user/repo"
        assert config.github_head_branch == "develop"
        assert config.enable_sandbox == False
        assert config.max_fix_attempts == 5
        assert config.rag_top_k == 10
    
    def test_validate_missing_keys(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        
        config = Config()
        errors = config.validate()
        
        assert len(errors) >= 3
        assert any("OPENAI_API_KEY" in e for e in errors)
        assert any("GITHUB_TOKEN" in e for e in errors)
        assert any("GITHUB_REPOSITORY" in e for e in errors)
    
    def test_validate_success(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")
        monkeypatch.setenv("GITHUB_REPOSITORY", "user/repo")
        
        config = Config()
        errors = config.validate()
        
        assert len(errors) == 0
    
    def test_allowed_extensions(self):
        config = Config()
        
        assert ".py" in config.allowed_file_extensions
        assert ".js" in config.allowed_file_extensions
        assert ".java" in config.allowed_file_extensions
    
    def test_security_severity_threshold(self):
        config = Config()
        
        assert config.security_severity_threshold == "medium"
    
    def test_sandbox_timeout(self):
        config = Config()
        
        assert config.sandbox_timeout == 300
