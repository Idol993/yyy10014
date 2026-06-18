import os
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class Config:
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4-turbo-preview"))
    openai_temperature: float = field(default_factory=lambda: float(os.getenv("OPENAI_TEMPERATURE", "0.1")))
    
    github_token: str = field(default_factory=lambda: os.getenv("GITHUB_TOKEN", ""))
    github_repository: str = field(default_factory=lambda: os.getenv("GITHUB_REPOSITORY", ""))
    github_run_id: str = field(default_factory=lambda: os.getenv("GITHUB_RUN_ID", ""))
    github_head_branch: str = field(default_factory=lambda: os.getenv("GITHUB_HEAD_BRANCH", "main"))
    github_head_sha: str = field(default_factory=lambda: os.getenv("GITHUB_HEAD_SHA", ""))
    
    build_log_path: str = field(default_factory=lambda: os.getenv("BUILD_LOG_PATH", "./build.log"))
    repo_path: str = field(default_factory=lambda: os.getenv("REPO_PATH", "."))
    
    enable_sandbox: bool = field(default_factory=lambda: os.getenv("ENABLE_SANDBOX", "true").lower() == "true")
    enable_security_scan: bool = field(default_factory=lambda: os.getenv("ENABLE_SECURITY_SCAN", "true").lower() == "true")
    enable_compatibility_check: bool = field(default_factory=lambda: os.getenv("ENABLE_COMPATIBILITY_CHECK", "true").lower() == "true")
    enable_test_run: bool = field(default_factory=lambda: os.getenv("ENABLE_TEST_RUN", "true").lower() == "true")
    
    max_fix_attempts: int = field(default_factory=lambda: int(os.getenv("MAX_FIX_ATTEMPTS", "3")))
    max_patch_size: int = field(default_factory=lambda: int(os.getenv("MAX_PATCH_SIZE", "500")))
    
    rag_chunk_size: int = field(default_factory=lambda: int(os.getenv("RAG_CHUNK_SIZE", "1000")))
    rag_chunk_overlap: int = field(default_factory=lambda: int(os.getenv("RAG_CHUNK_OVERLAP", "200")))
    rag_top_k: int = field(default_factory=lambda: int(os.getenv("RAG_TOP_K", "5")))
    
    security_severity_threshold: str = field(default_factory=lambda: os.getenv("SECURITY_SEVERITY_THRESHOLD", "medium"))
    
    sandbox_timeout: int = field(default_factory=lambda: int(os.getenv("SANDBOX_TIMEOUT", "300")))
    
    allowed_file_extensions: List[str] = field(default_factory=lambda: 
        os.getenv("ALLOWED_EXTENSIONS", ".py,.js,.ts,.java,.go,.rs,.cpp,.c").split(","))
    
    def validate(self) -> List[str]:
        errors = []
        if not self.openai_api_key:
            errors.append("OPENAI_API_KEY is required")
        if not self.github_token:
            errors.append("GITHUB_TOKEN is required")
        if not self.github_repository:
            errors.append("GITHUB_REPOSITORY is required")
        return errors
