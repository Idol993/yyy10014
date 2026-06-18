import os
import sys
import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from datetime import datetime

from src.config import Config
from src.log_parser import BuildLogParser
from src.ast_diff import ASTComparator
from src.rag import DocumentRetriever
from src.fix_generator import LLMFixer, FixPatch
from src.sandbox import (
    SecurityScanner, SecurityFinding,
    CompatibilityChecker, CompatibilityIssue,
    TestRunner, TestResult,
)
from src.pr_submitter import GitHubPRSubmitter, PRCreationResult


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class FixWorkflowResult:
    success: bool = False
    pr_created: bool = False
    pr_url: Optional[str] = None
    errors_found: int = 0
    patches_generated: int = 0
    security_issues: int = 0
    compatibility_issues: int = 0
    tests_passed: Optional[bool] = None
    error_message: str = ""
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "pr_created": self.pr_created,
            "pr_url": self.pr_url,
            "errors_found": self.errors_found,
            "patches_generated": self.patches_generated,
            "security_issues": self.security_issues,
            "compatibility_issues": self.compatibility_issues,
            "tests_passed": self.tests_passed,
            "error_message": self.error_message,
        }


class CICDFixBot:
    def __init__(self, config: Config):
        self.config = config
        self.log_parser = BuildLogParser(config.build_log_path)
        self.ast_comparator = ASTComparator(config.repo_path)
        self.doc_retriever = DocumentRetriever(
            repo_path=config.repo_path,
            openai_api_key=config.openai_api_key,
            chunk_size=config.rag_chunk_size,
            chunk_overlap=config.rag_chunk_overlap,
            top_k=config.rag_top_k,
        )
        self.llm_fixer = LLMFixer(
            openai_api_key=config.openai_api_key,
            model=config.openai_model,
            temperature=config.openai_temperature,
        )
        self.security_scanner = SecurityScanner(
            repo_path=config.repo_path,
            severity_threshold=config.security_severity_threshold,
        )
        self.compatibility_checker = CompatibilityChecker(
            repo_path=config.repo_path,
        )
        self.test_runner = TestRunner(
            repo_path=config.repo_path,
            timeout=config.sandbox_timeout,
        )
        self.pr_submitter = GitHubPRSubmitter(
            token=config.github_token,
            repo=config.github_repository,
            repo_path=config.repo_path,
        )
    
    def run(self) -> FixWorkflowResult:
        result = FixWorkflowResult()
        
        try:
            logger.info("Starting CI/CD Fix Bot workflow...")
            
            logger.info("Step 1: Parsing build logs...")
            parsed_log = self.log_parser.parse()
            result.errors_found = parsed_log.error_count
            
            if not parsed_log.has_errors:
                logger.info("No build errors found. Exiting.")
                result.success = True
                return result
            
            logger.info(f"Found {parsed_log.error_count} build errors.")
            
            errors_by_file = self.log_parser.get_errors_by_file()
            
            logger.info("Step 2: Analyzing recent code changes...")
            change_analysis = self._analyze_code_changes()
            
            logger.info("Step 3: Indexing project documentation...")
            self.doc_retriever.index_project()
            
            all_patches: List[FixPatch] = []
            error_summary_parts = []
            
            logger.info("Step 4: Generating fixes for each error...")
            
            for file_path, errors in errors_by_file.items():
                logger.info(f"Processing {len(errors)} errors in {file_path}")
                
                file_content = self._read_file(file_path)
                
                if not file_content:
                    logger.warning(f"Could not read file: {file_path}")
                    continue
                
                for error in errors:
                    error_summary_parts.append(f"{error.error_type}: {error.message} in {file_path}:{error.line_number}")
                    
                    code_changes = self.ast_comparator.find_changes_for_error(
                        file_path, error.line_number or 0, change_analysis
                    )
                    
                    context = self.doc_retriever.retrieve_for_error(
                        error_type=error.error_type,
                        error_message=error.message,
                        file_path=file_path,
                        function_name=self._get_error_function(error, code_changes),
                    )
                    
                    fix_result = self.llm_fixer.generate_fix(
                        error=error,
                        code_changes=code_changes,
                        context=context,
                        file_content=file_content,
                        max_attempts=1,
                    )
                    
                    if fix_result.has_patches:
                        all_patches.extend(fix_result.valid_patches)
                        logger.info(f"Generated {len(fix_result.valid_patches)} patches for {error.error_type}")
            
            result.patches_generated = len(all_patches)
            
            if not all_patches:
                result.error_message = "Could not generate any valid patches"
                logger.warning("No valid patches generated.")
                return result
            
            logger.info(f"Generated {len(all_patches)} total patches.")
            
            if self.config.enable_sandbox:
                logger.info("Step 5: Running sandbox validation...")
                
                if self.config.enable_security_scan:
                    logger.info("Step 5a: Security scan...")
                    files_to_scan = list(set(p.file_path for p in all_patches))
                    security_result = self.security_scanner.scan_changed_files(files_to_scan)
                    result.security_issues = security_result.total_findings
                    
                    if security_result.has_blocking_issues:
                        logger.warning(f"Security scan found {security_result.total_findings} issues, "
                                      f"including blocking issues.")
                    else:
                        logger.info(f"Security scan passed. {security_result.total_findings} findings (none blocking).")
                
                if self.config.enable_compatibility_check:
                    logger.info("Step 5b: Compatibility check...")
                    compat_issues = self._check_compatibility(all_patches)
                    result.compatibility_issues = len(compat_issues)
                    
                    if compat_issues:
                        logger.warning(f"Found {len(compat_issues)} compatibility issues.")
                    else:
                        logger.info("Compatibility check passed. No breaking changes detected.")
                
                if self.config.enable_test_run:
                    logger.info("Step 5c: Running tests...")
                    changed_files = list(set(p.file_path for p in all_patches))
                    test_result = self.test_runner.run_relevant_tests(changed_files)
                    result.tests_passed = test_result.all_passed
                    
                    if test_result.all_passed:
                        logger.info(f"All tests passed ({test_result.passed}/{test_result.total_tests}).")
                    else:
                        logger.warning(f"Some tests failed ({test_result.passed}/{test_result.total_tests} passed).")
            
            logger.info("Step 6: Creating pull request...")
            error_summary = "\n".join(error_summary_parts[:10])
            
            pr_result = self.pr_submitter.create_fix_pr(
                patches=all_patches,
                security_result=None,
                compatibility_report=None,
                test_result=None,
                error_summary=error_summary,
                base_branch=self.config.github_head_branch,
            )
            
            result.pr_created = pr_result.success
            result.pr_url = pr_result.pr_url
            
            if pr_result.success:
                logger.info(f"PR created successfully: {pr_result.pr_url}")
                result.success = True
            else:
                result.error_message = pr_result.error_message
                logger.error(f"Failed to create PR: {pr_result.error_message}")
            
        except Exception as e:
            result.success = False
            result.error_message = str(e)
            logger.error(f"Workflow failed: {e}", exc_info=True)
        
        return result
    
    def _analyze_code_changes(self):
        if self.config.github_head_sha:
            try:
                import git
                repo = git.Repo(self.config.repo_path)
                
                head_sha = self.config.github_head_sha
                parent_sha = f"{head_sha}^"
                
                analysis = self.ast_comparator.compare_commits(parent_sha, head_sha)
                
                logger.info(f"Analyzed changes: {analysis.total_changes} changes in "
                           f"{len(analysis.modified_files)} files")
                
                return analysis
            except Exception as e:
                logger.warning(f"Could not analyze commit changes: {e}")
        
        from src.ast_diff import ChangeAnalysis
        return ChangeAnalysis()
    
    def _read_file(self, file_path: str) -> Optional[str]:
        full_path = os.path.join(self.config.repo_path, file_path)
        
        if not os.path.exists(full_path):
            full_path = file_path
        
        if os.path.exists(full_path):
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    return f.read()
            except Exception:
                pass
        
        return None
    
    def _get_error_function(self, error, code_changes) -> Optional[str]:
        for change in code_changes:
            if change.function_name:
                return change.function_name
        return None
    
    def _check_compatibility(self, patches: List[FixPatch]) -> List:
        issues = []
        
        for patch in patches:
            file_path = patch.file_path
            new_content = self._read_file(file_path)
            
            if not new_content:
                continue
            
            try:
                import git
                repo = git.Repo(self.config.repo_path)
                head_sha = self.config.github_head_sha or "HEAD"
                
                old_content = repo.git.show(f"{head_sha}:{file_path}")
                
                report = self.compatibility_checker.check_compatibility(
                    old_content, new_content, file_path
                )
                
                issues.extend(report.issues)
                
            except Exception as e:
                logger.warning(f"Could not check compatibility for {file_path}: {e}")
        
        return issues


def main():
    config = Config()
    
    errors = config.validate()
    if errors:
        logger.error("Configuration validation failed:")
        for error in errors:
            logger.error(f"  - {error}")
        sys.exit(1)
    
    bot = CICDFixBot(config)
    result = bot.run()
    
    logger.info("=" * 60)
    logger.info("Workflow Summary:")
    logger.info(f"  Success: {result.success}")
    logger.info(f"  PR Created: {result.pr_created}")
    if result.pr_url:
        logger.info(f"  PR URL: {result.pr_url}")
    logger.info(f"  Errors Found: {result.errors_found}")
    logger.info(f"  Patches Generated: {result.patches_generated}")
    logger.info(f"  Security Issues: {result.security_issues}")
    logger.info(f"  Compatibility Issues: {result.compatibility_issues}")
    if result.tests_passed is not None:
        logger.info(f"  Tests Passed: {result.tests_passed}")
    if result.error_message:
        logger.info(f"  Error: {result.error_message}")
    logger.info("=" * 60)
    
    output_file = os.environ.get("GITHUB_OUTPUT", "")
    if output_file:
        with open(output_file, "a") as f:
            f.write(f"pr_created={str(result.pr_created).lower()}\n")
            if result.pr_url:
                f.write(f"pr_url={result.pr_url}\n")
            f.write(f"errors_found={result.errors_found}\n")
            f.write(f"patches_generated={result.patches_generated}\n")
            f.write(f"success={str(result.success).lower()}\n")
    
    result_file = os.path.join(config.repo_path, "cicd-fix-result.json")
    with open(result_file, "w") as f:
        json.dump(result.to_dict(), f, indent=2)
    
    logger.info(f"Result saved to: {result_file}")
    
    if not result.success and not result.pr_created:
        sys.exit(1)


if __name__ == "__main__":
    main()
