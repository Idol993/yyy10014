import os
import sys
import json
import logging
import tempfile
import shutil
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from datetime import datetime

from src.config import Config
from src.log_parser import BuildLogParser
from src.ast_diff import ASTComparator
from src.rag import DocumentRetriever
from src.fix_generator import LLMFixer, FixPatch
from src.sandbox import (
    SecurityScanner, SecurityFinding, Severity,
    CompatibilityChecker, CompatibilityIssue, CompatibilityReport,
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
    security_blocking: bool = False
    compatibility_issues: int = 0
    compatibility_breaking: bool = False
    tests_passed: Optional[bool] = None
    sandbox_blocked: bool = False
    blocked_reason: str = ""
    error_message: str = ""
    error_step: str = ""
    pr_push_failed: bool = False
    pr_create_failed: bool = False
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "pr_created": self.pr_created,
            "pr_url": self.pr_url,
            "errors_found": self.errors_found,
            "patches_generated": self.patches_generated,
            "security_issues": self.security_issues,
            "security_blocking": self.security_blocking,
            "compatibility_issues": self.compatibility_issues,
            "compatibility_breaking": self.compatibility_breaking,
            "tests_passed": self.tests_passed,
            "sandbox_blocked": self.sandbox_blocked,
            "blocked_reason": self.blocked_reason,
            "error_message": self.error_message,
            "error_step": self.error_step,
            "pr_push_failed": self.pr_push_failed,
            "pr_create_failed": self.pr_create_failed,
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
        temp_dir = None
        
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
            
            logger.info("Step 3: Indexing project documentation (RAG)...")
            try:
                self.doc_retriever.index_project()
            except Exception as e:
                logger.warning(f"RAG indexing had issues (continuing anyway): {e}")
            
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
                    
                    try:
                        context = self.doc_retriever.retrieve_for_error(
                            error_type=error.error_type,
                            error_message=error.message,
                            file_path=file_path,
                            function_name=self._get_error_function(error, code_changes),
                        )
                    except Exception as e:
                        logger.warning(f"RAG retrieval failed for error: {e}")
                        from src.rag import RetrievalResult
                        context = RetrievalResult()
                    
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
                logger.info("Step 5: Running sandbox validation in isolated temporary directory...")
                
                temp_dir = tempfile.mkdtemp(prefix="cicd_fix_sandbox_")
                logger.info(f"Created sandbox directory: {temp_dir}")
                
                self._copy_repo_to_temp(temp_dir)
                self._apply_patches_to_temp(temp_dir, all_patches)
                logger.info("Patches applied to sandbox directory")
                
                security_result = None
                compat_report = None
                test_result = None
                
                if self.config.enable_security_scan:
                    logger.info("Step 5a: Security scan (on patched code)...")
                    temp_scanner = SecurityScanner(
                        repo_path=temp_dir,
                        severity_threshold=self.config.security_severity_threshold,
                    )
                    files_to_scan = list(set(p.file_path for p in all_patches))
                    security_result = temp_scanner.scan_changed_files(files_to_scan)
                    result.security_issues = security_result.total_findings
                    result.security_blocking = security_result.has_blocking_issues
                    
                    if security_result.has_blocking_issues:
                        critical_count = len(security_result.get_findings_by_severity(Severity.CRITICAL))
                        high_count = len(security_result.get_findings_by_severity(Severity.HIGH))
                        logger.error(f"❌ SECURITY SCAN FAILED - Found {security_result.total_findings} issues "
                                    f"({critical_count} critical, {high_count} high)")
                    else:
                        logger.info(f"✅ Security scan passed. {security_result.total_findings} findings (none blocking).")
                
                if self.config.enable_compatibility_check:
                    logger.info("Step 5b: Compatibility check (on patched code)...")
                    temp_checker = CompatibilityChecker(repo_path=temp_dir)
                    compat_issues, compat_report = self._check_compatibility_in_sandbox(
                        temp_dir, all_patches
                    )
                    result.compatibility_issues = len(compat_issues)
                    result.compatibility_breaking = compat_report.has_breaking_changes if compat_report else False
                    
                    if compat_report and compat_report.has_breaking_changes:
                        logger.error(f"❌ COMPATIBILITY CHECK FAILED - Found {len(compat_issues)} breaking changes")
                        if compat_report.removed_apis:
                            logger.error(f"   Removed APIs: {', '.join(compat_report.removed_apis)}")
                        if compat_report.modified_signatures:
                            logger.error(f"   Modified signatures: {', '.join(compat_report.modified_signatures)}")
                    else:
                        logger.info("✅ Compatibility check passed. No breaking changes detected.")
                
                if self.config.enable_test_run:
                    logger.info("Step 5c: Running tests (on patched code)...")
                    try:
                        temp_runner = TestRunner(
                            repo_path=temp_dir,
                            timeout=self.config.sandbox_timeout,
                        )
                        changed_files = list(set(p.file_path for p in all_patches))
                        test_result = temp_runner.run_relevant_tests(changed_files)
                        result.tests_passed = test_result.all_passed
                        
                        if test_result.all_passed:
                            logger.info(f"✅ All tests passed ({test_result.passed}/{test_result.total_tests}).")
                        else:
                            logger.error(f"❌ TESTS FAILED - Only {test_result.passed}/{test_result.total_tests} tests passed")
                    except Exception as e:
                        test_result = TestResult()
                        test_result.failed = 1
                        test_result.errors = 1
                        test_result.total_tests = 1
                        test_result.output = f"Test runner crashed: {e}"
                        result.tests_passed = False
                        logger.error(f"❌ Test runner exception: {e}")
                
                blocked_reasons = []
                if result.security_blocking:
                    blocked_reasons.append(f"Security scan found blocking issues ({result.security_issues} total, including critical/high severity)")
                if result.compatibility_breaking:
                    blocked_reasons.append(f"Compatibility check found {result.compatibility_issues} breaking changes to public API")
                if result.tests_passed is False:
                    blocked_reasons.append(f"Tests failed ({test_result.passed if test_result else 0}/{test_result.total_tests if test_result else 0} passed)")
                
                if blocked_reasons:
                    result.sandbox_blocked = True
                    result.blocked_reason = "; ".join(blocked_reasons)
                    result.error_message = f"Sandbox validation blocked: {result.blocked_reason}"
                    logger.error(f"🚫 PR submission blocked by sandbox validation")
                    logger.error(f"   Reason: {result.blocked_reason}")
                    logger.error("   No Pull Request will be created.")
                    return result
            
            logger.info("Step 6: Creating pull request...")
            error_summary = "\n".join(error_summary_parts[:10])
            
            pr_result = self.pr_submitter.create_fix_pr(
                patches=all_patches,
                security_result=security_result,
                compatibility_report=compat_report,
                test_result=test_result,
                error_summary=error_summary,
                base_branch=self.config.github_head_branch,
            )
            
            result.pr_created = pr_result.success
            result.pr_url = pr_result.pr_url
            result.error_step = pr_result.error_step if hasattr(pr_result, 'error_step') else ""
            result.pr_push_failed = result.error_step == "push_branch"
            result.pr_create_failed = result.error_step == "create_pr_api"
            
            if pr_result.success:
                logger.info(f"✅ PR created successfully: {pr_result.pr_url}")
                result.success = True
            else:
                result.error_message = f"PR creation failed: {pr_result.error_message}"
                if result.error_step:
                    logger.error(f"❌ Failed at step: {result.error_step}")
                if result.pr_push_failed:
                    logger.error(f"❌ Failed to push branch to remote: {pr_result.error_message}")
                elif result.pr_create_failed:
                    logger.error(f"❌ Failed to create PR via GitHub API: {pr_result.error_message}")
                else:
                    logger.error(f"❌ Failed to create PR: {pr_result.error_message}")
            
        except Exception as e:
            result.success = False
            result.error_message = str(e)
            logger.error(f"❌ Workflow failed with exception: {e}", exc_info=True)
        
        finally:
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    logger.info(f"Cleaned up sandbox directory: {temp_dir}")
                except Exception as e:
                    logger.warning(f"Could not clean up sandbox directory: {e}")
        
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
    
    def _read_file_from_dir(self, base_dir: str, file_path: str) -> Optional[str]:
        full_path = os.path.join(base_dir, file_path)
        
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
    
    def _copy_repo_to_temp(self, temp_dir: str) -> None:
        exclude_dirs = {'.git', 'node_modules', '__pycache__', 'venv', '.venv', 'build', 'dist', 'target'}
        exclude_patterns = {'*.pyc', '*.pyo', '*.class', '*.o', '*.so', '*.dll', '*.exe'}
        
        for item in os.listdir(self.config.repo_path):
            src = os.path.join(self.config.repo_path, item)
            dst = os.path.join(temp_dir, item)
            
            if item in exclude_dirs:
                continue
            
            if os.path.isdir(src):
                try:
                    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*exclude_patterns))
                except Exception as e:
                    logger.warning(f"Could not copy directory {item}: {e}")
            else:
                try:
                    shutil.copy2(src, dst)
                except Exception as e:
                    logger.warning(f"Could not copy file {item}: {e}")
        
        logger.info(f"Repository copied to sandbox: {temp_dir}")
    
    def _apply_patches_to_temp(self, temp_dir: str, patches: List[FixPatch]) -> None:
        for patch in patches:
            if not patch.is_valid:
                continue
            
            file_path = os.path.join(temp_dir, patch.file_path)
            
            if not os.path.exists(file_path):
                logger.warning(f"Patch target file not found in sandbox: {patch.file_path}")
                continue
            
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                
                new_content = self.llm_fixer.apply_patch(patch, content)
                
                if new_content != content:
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    logger.info(f"Applied patch to: {patch.file_path}")
                else:
                    logger.warning(f"Patch did not change content: {patch.file_path}")
                    
            except Exception as e:
                logger.error(f"Failed to apply patch to {patch.file_path}: {e}")
    
    def _check_compatibility_in_sandbox(self, temp_dir: str, patches: List[FixPatch]) -> tuple:
        all_issues = []
        final_report = None
        
        try:
            import git
            repo = git.Repo(self.config.repo_path)
            head_sha = self.config.github_head_sha or "HEAD"
            
            for patch in patches:
                file_path = patch.file_path
                
                try:
                    old_content = repo.git.show(f"{head_sha}:{file_path}")
                except Exception:
                    old_content = self._read_file(file_path)
                
                new_content = self._read_file_from_dir(temp_dir, file_path)
                
                if old_content and new_content:
                    report = self.compatibility_checker.check_compatibility(
                        old_content, new_content, file_path
                    )
                    all_issues.extend(report.issues)
                    if final_report is None:
                        final_report = report
                    else:
                        final_report.issues.extend(report.issues)
                        final_report.removed_apis.extend(report.removed_apis)
                        final_report.modified_signatures.extend(report.modified_signatures)
                        
        except Exception as e:
            logger.warning(f"Compatibility check had issues: {e}")
        
        return all_issues, final_report


def main():
    config = Config()
    
    errors = config.validate()
    if errors:
        logger.error("❌ Configuration validation failed:")
        for error in errors:
            logger.error(f"   - {error}")
        sys.exit(1)
    
    bot = CICDFixBot(config)
    result = bot.run()
    
    logger.info("")
    logger.info("=" * 60)
    logger.info("📊 Workflow Summary:")
    logger.info("=" * 60)
    logger.info(f"  Success:            {'✅ YES' if result.success else '❌ NO'}")
    logger.info(f"  PR Created:         {'✅ YES' if result.pr_created else '❌ NO'}")
    if result.pr_url:
        logger.info(f"  PR URL:             {result.pr_url}")
    logger.info(f"  Errors Found:       {result.errors_found}")
    logger.info(f"  Patches Generated:  {result.patches_generated}")
    logger.info(f"  Security Issues:    {result.security_issues}" + 
                (" (BLOCKING ⚠️)" if result.security_blocking else ""))
    logger.info(f"  Compatibility:      {result.compatibility_issues} issues" +
                (" (BREAKING ⚠️)" if result.compatibility_breaking else ""))
    if result.tests_passed is not None:
        logger.info(f"  Tests Passed:       {'✅ YES' if result.tests_passed else '❌ NO'}")
    
    if result.sandbox_blocked:
        logger.error("")
        logger.error("🚫 SANDBOX BLOCKED PR SUBMISSION")
        logger.error(f"   Reason: {result.blocked_reason}")
    
    if result.pr_push_failed:
        logger.error("")
        logger.error("❌ FAILED TO PUSH BRANCH TO REMOTE")
    if result.pr_create_failed:
        logger.error("")
        logger.error("❌ FAILED TO CREATE PR VIA GITHUB API")
    
    if result.error_step:
        logger.error(f"  Failed at step:      {result.error_step}")
    if result.error_message:
        logger.error(f"  Error Message:      {result.error_message}")
    logger.info("=" * 60)
    logger.info("")
    
    output_file = os.environ.get("GITHUB_OUTPUT", "")
    if output_file:
        with open(output_file, "a") as f:
            f.write(f"pr_created={str(result.pr_created).lower()}\n")
            if result.pr_url:
                f.write(f"pr_url={result.pr_url}\n")
            f.write(f"errors_found={result.errors_found}\n")
            f.write(f"patches_generated={result.patches_generated}\n")
            f.write(f"security_issues={result.security_issues}\n")
            f.write(f"security_blocking={str(result.security_blocking).lower()}\n")
            f.write(f"compatibility_issues={result.compatibility_issues}\n")
            f.write(f"compatibility_breaking={str(result.compatibility_breaking).lower()}\n")
            if result.tests_passed is not None:
                f.write(f"tests_passed={str(result.tests_passed).lower()}\n")
            f.write(f"sandbox_blocked={str(result.sandbox_blocked).lower()}\n")
            if result.blocked_reason:
                f.write(f"blocked_reason={result.blocked_reason}\n")
            f.write(f"pr_push_failed={str(result.pr_push_failed).lower()}\n")
            f.write(f"pr_create_failed={str(result.pr_create_failed).lower()}\n")
            if result.error_step:
                f.write(f"error_step={result.error_step}\n")
            f.write(f"success={str(result.success).lower()}\n")
    
    result_file = os.path.join(config.repo_path, "cicd-fix-result.json")
    with open(result_file, "w") as f:
        json.dump(result.to_dict(), f, indent=2)
    
    logger.info(f"📄 Detailed result saved to: {result_file}")
    logger.info("")
    
    if not result.success and not result.pr_created:
        sys.exit(1)


if __name__ == "__main__":
    main()
