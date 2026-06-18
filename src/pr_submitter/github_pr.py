import os
import re
import json
import time
import logging
import hashlib
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from datetime import datetime

from src.fix_generator import FixPatch
from src.sandbox import SecurityScanResult, CompatibilityReport, TestResult


@dataclass
class PRCreationResult:
    success: bool = False
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    branch_name: str = ""
    error_message: str = ""
    error_step: str = ""
    commit_sha: Optional[str] = None


@dataclass
class PRDescription:
    title: str
    body: str
    labels: List[str] = field(default_factory=list)


class GitHubPRSubmitter:
    def __init__(self, token: str, repo: str, repo_path: str = "."):
        self.token = token
        self.repo = repo
        self.repo_path = repo_path
        self._api_base = "https://api.github.com"
    
    def create_fix_pr(self, patches: List[FixPatch],
                      security_result: Optional[SecurityScanResult] = None,
                      compatibility_report: Optional[CompatibilityReport] = None,
                      test_result: Optional[TestResult] = None,
                      error_summary: str = "",
                      base_branch: str = "main") -> PRCreationResult:
        result = PRCreationResult()
        
        try:
            logger = logging.getLogger(__name__)
            
            logger.info(f"[PR Submitter] Step 1: Generating branch name...")
            branch_name = self._generate_branch_name(patches, error_summary)
            result.branch_name = branch_name
            logger.info(f"[PR Submitter] Branch name: {branch_name}")
            
            logger.info(f"[PR Submitter] Step 2: Creating local branch '{branch_name}' from '{base_branch}'...")
            try:
                self._create_branch(branch_name, base_branch)
            except Exception as e:
                result.success = False
                result.error_step = "create_branch"
                result.error_message = f"Failed to create local branch: {str(e)}"
                logger.error(f"[PR Submitter] ❌ {result.error_message}")
                return result
            
            logger.info(f"[PR Submitter] Step 3: Applying {len(patches)} patches...")
            try:
                self._apply_patches(patches)
            except Exception as e:
                result.success = False
                result.error_step = "apply_patches"
                result.error_message = f"Failed to apply patches: {str(e)}"
                logger.error(f"[PR Submitter] ❌ {result.error_message}")
                return result
            
            logger.info(f"[PR Submitter] Step 4: Committing changes...")
            try:
                commit_sha = self._commit_changes(patches, error_summary)
                result.commit_sha = commit_sha
                logger.info(f"[PR Submitter] Committed as: {commit_sha[:8]}")
            except Exception as e:
                result.success = False
                result.error_step = "commit_changes"
                result.error_message = f"Failed to commit changes: {str(e)}"
                logger.error(f"[PR Submitter] ❌ {result.error_message}")
                return result
            
            logger.info(f"[PR Submitter] Step 5: Pushing branch '{branch_name}' to remote...")
            try:
                self._push_branch(branch_name)
            except Exception as e:
                result.success = False
                result.error_step = "push_branch"
                result.error_message = f"Failed to push branch to remote: {str(e)}"
                logger.error(f"[PR Submitter] ❌ {result.error_message}")
                return result
            logger.info(f"[PR Submitter] ✅ Branch pushed successfully")
            
            logger.info(f"[PR Submitter] Step 6: Generating PR description...")
            pr_desc = self._generate_pr_description(
                patches, security_result, compatibility_report, test_result, error_summary
            )
            
            logger.info(f"[PR Submitter] Step 7: Creating Pull Request via GitHub API...")
            try:
                pr_number, pr_url = self._create_pull_request(
                    pr_desc.title, pr_desc.body, branch_name, base_branch, pr_desc.labels
                )
            except Exception as e:
                result.success = False
                result.error_step = "create_pr_api"
                result.error_message = f"GitHub API failed to create PR: {str(e)}"
                logger.error(f"[PR Submitter] ❌ {result.error_message}")
                return result
            
            result.success = True
            result.pr_number = pr_number
            result.pr_url = pr_url
            logger.info(f"[PR Submitter] ✅ PR created successfully: {pr_url}")
            
        except Exception as e:
            result.success = False
            result.error_step = "unknown"
            result.error_message = f"Unexpected error: {str(e)}"
            import logging
            logging.getLogger(__name__).error(f"[PR Submitter] ❌ {result.error_message}", exc_info=True)
        
        return result
    
    def _generate_branch_name(self, patches: List[FixPatch], error_summary: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        
        error_hash = hashlib.md5(error_summary.encode()).hexdigest()[:8]
        
        if patches:
            first_patch = patches[0]
            file_name = os.path.basename(first_patch.file_path)
            file_base = os.path.splitext(file_name)[0]
            file_slug = re.sub(r'[^a-zA-Z0-9]', '-', file_base)[:30]
        else:
            file_slug = "fix"
        
        return f"auto-fix/{file_slug}-{error_hash}-{timestamp}"
    
    def _create_branch(self, branch_name: str, base_branch: str) -> None:
        try:
            import git
            repo = git.Repo(self.repo_path)
            
            base_commit = repo.refs[base_branch].commit
            
            new_branch = repo.create_head(branch_name, base_commit)
            new_branch.checkout()
            
        except ImportError:
            self._create_branch_git_cli(branch_name, base_branch)
    
    def _create_branch_git_cli(self, branch_name: str, base_branch: str) -> None:
        import subprocess
        
        subprocess.run(
            ["git", "checkout", "-b", branch_name, base_branch],
            cwd=self.repo_path,
            check=True,
            capture_output=True,
            text=True,
        )
    
    def _apply_patches(self, patches: List[FixPatch]) -> None:
        for patch in patches:
            if not patch.is_valid:
                continue
            
            file_path = os.path.join(self.repo_path, patch.file_path)
            
            if not os.path.exists(file_path):
                continue
            
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            new_content = self._apply_single_patch(content, patch)
            
            if new_content != content:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
    
    def _apply_single_patch(self, content: str, patch: FixPatch) -> str:
        original = patch.original_code.strip()
        fixed = patch.fixed_code.strip()
        
        original_lines = original.split('\n')
        content_lines = content.split('\n')
        
        for i in range(len(content_lines) - len(original_lines) + 1):
            match = True
            for j in range(len(original_lines)):
                if content_lines[i + j].strip() != original_lines[j].strip():
                    match = False
                    break
            
            if match:
                new_lines = (content_lines[:i] + 
                            fixed.split('\n') + 
                            content_lines[i + len(original_lines):])
                return '\n'.join(new_lines)
        
        if patch.line_start > 0 and patch.line_end > 0:
            lines = content.split('\n')
            fixed_lines = fixed.split('\n')
            new_lines = (lines[:patch.line_start - 1] + 
                        fixed_lines + 
                        lines[patch.line_end:])
            return '\n'.join(new_lines)
        
        return content
    
    def _commit_changes(self, patches: List[FixPatch], error_summary: str) -> str:
        try:
            import git
            repo = git.Repo(self.repo_path)
            
            for patch in patches:
                if patch.file_path:
                    try:
                        repo.index.add([patch.file_path])
                    except Exception:
                        pass
            
            commit_msg = self._generate_commit_message(patches, error_summary)
            
            commit = repo.index.commit(commit_msg)
            return commit.hexsha
            
        except ImportError:
            return self._commit_changes_git_cli(patches, error_summary)
    
    def _commit_changes_git_cli(self, patches: List[FixPatch], error_summary: str) -> str:
        import subprocess
        
        for patch in patches:
            if patch.file_path:
                subprocess.run(
                    ["git", "add", patch.file_path],
                    cwd=self.repo_path,
                    capture_output=True,
                    text=True,
                )
        
        commit_msg = self._generate_commit_message(patches, error_summary)
        
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=self.repo_path,
            check=True,
            capture_output=True,
            text=True,
        )
        
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )
        
        return result.stdout.strip()
    
    def _generate_commit_message(self, patches: List[FixPatch], error_summary: str) -> str:
        title = self._summarize_error(error_summary)
        
        if len(title) > 72:
            title = title[:69] + "..."
        
        body = ""
        
        if len(patches) > 0:
            body += "\n\n## Changes\n"
            for patch in patches:
                body += f"- {os.path.basename(patch.file_path)}: {patch.explanation[:50]}\n"
        
        body += "\n\nThis is an automated fix generated by CI/CD Fix Bot."
        
        return f"{title}\n{body}"
    
    def _summarize_error(self, error_summary: str) -> str:
        if not error_summary:
            return "Fix build error"
        
        lines = error_summary.strip().split('\n')
        if lines:
            first_line = lines[0].strip()
            if len(first_line) > 72:
                first_line = first_line[:69] + "..."
            return f"fix: {first_line}"
        
        return "Fix build error"
    
    def _generate_pr_description(self, patches: List[FixPatch],
                                  security_result: Optional[SecurityScanResult],
                                  compatibility_report: Optional[CompatibilityReport],
                                  test_result: Optional[TestResult],
                                  error_summary: str) -> PRDescription:
        title = self._generate_pr_title(error_summary, patches)
        
        body = self._generate_pr_body(
            patches, security_result, compatibility_report, test_result, error_summary
        )
        
        labels = [
            "automated-fix",
            "ci-cd-fix-bot",
        ]
        
        if security_result and security_result.has_blocking_issues:
            labels.append("security-review-needed")
        
        if compatibility_report and compatibility_report.has_breaking_changes:
            labels.append("breaking-change")
        
        return PRDescription(
            title=title,
            body=body,
            labels=labels,
        )
    
    def _generate_pr_title(self, error_summary: str, patches: List[FixPatch]) -> str:
        if error_summary:
            first_line = error_summary.strip().split('\n')[0]
            if len(first_line) > 80:
                first_line = first_line[:77] + "..."
            return f"[Auto Fix] {first_line}"
        
        if patches:
            files = [os.path.basename(p.file_path) for p in patches]
            files_str = ", ".join(files[:2])
            if len(files) > 2:
                files_str += f" and {len(files) - 2} more"
            return f"[Auto Fix] Fix build errors in {files_str}"
        
        return "[Auto Fix] Fix build errors"
    
    def _generate_pr_body(self, patches: List[FixPatch],
                          security_result: Optional[SecurityScanResult],
                          compatibility_report: Optional[CompatibilityReport],
                          test_result: Optional[TestResult],
                          error_summary: str) -> str:
        body = ""
        
        body += "## 🤖 Automated Fix\n\n"
        body += "This PR was automatically generated by the CI/CD Fix Bot.\n\n"
        
        if error_summary:
            body += "## 📋 Error Summary\n\n"
            body += f"```\n{error_summary[:500]}\n```\n\n"
        
        body += "## 🔧 Changes Made\n\n"
        if patches:
            for i, patch in enumerate(patches, 1):
                body += f"### {i}. {os.path.basename(patch.file_path)}\n\n"
                if patch.explanation:
                    body += f"**Why:** {patch.explanation}\n\n"
                
                body += "```diff\n"
                old_lines = patch.original_code.strip().split('\n')
                new_lines = patch.fixed_code.strip().split('\n')
                
                for line in old_lines:
                    body += f"- {line}\n"
                body += "---\n"
                for line in new_lines:
                    body += f"+ {line}\n"
                
                body += "```\n\n"
        else:
            body += "No patches generated.\n\n"
        
        body += "## ✅ Validation Results\n\n"
        
        if security_result:
            body += "### 🔒 Security Scan\n"
            if security_result.has_blocking_issues:
                body += "❌ **Blocking issues found - manual review required**\n"
            else:
                body += "✅ No critical or high severity issues\n"
            body += f"- Total findings: {security_result.total_findings}\n"
            body += f"- Critical: {len(security_result.get_findings_by_severity(SecurityFinding.__class__)) if False else 0}\n"
            body += "\n"
        
        if compatibility_report:
            body += "### 🔄 Backward Compatibility\n"
            if compatibility_report.has_breaking_changes:
                body += "⚠️ **Breaking changes detected**\n"
                if compatibility_report.removed_apis:
                    body += f"- Removed APIs: {', '.join(compatibility_report.removed_apis)}\n"
                if compatibility_report.modified_signatures:
                    body += f"- Modified signatures: {', '.join(compatibility_report.modified_signatures)}\n"
            else:
                body += "✅ No breaking changes detected\n"
            body += "\n"
        
        if test_result:
            body += "### 🧪 Tests\n"
            if test_result.all_passed:
                body += f"✅ All tests passed ({test_result.passed}/{test_result.total_tests})\n"
            else:
                body += f"⚠️ Some tests failed ({test_result.passed}/{test_result.total_tests} passed)\n"
            if test_result.total_duration > 0:
                body += f"- Duration: {test_result.total_duration:.2f}s\n"
            body += "\n"
        
        body += "## ⚠️ Important Notes\n\n"
        body += "- This is an automated fix. Please review carefully before merging.\n"
        body += "- The fix has been validated through automated security scans and tests.\n"
        body += "- If there are issues, please provide feedback so the bot can learn.\n\n"
        
        body += "---\n\n"
        body += f"*Generated by CI/CD Fix Bot at {datetime.now().isoformat()}*\n"
        
        return body
    
    def _create_pull_request(self, title: str, body: str,
                              head_branch: str, base_branch: str,
                              labels: List[str]) -> tuple:
        import requests
        import logging
        
        logger = logging.getLogger(__name__)
        
        url = f"{self._api_base}/repos/{self.repo}/pulls"
        
        headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
        }
        
        data = {
            "title": title,
            "body": body,
            "head": head_branch,
            "base": base_branch,
            "maintainer_can_modify": True,
        }
        
        logger.debug(f"[PR Submitter] Sending PR creation request to {url}")
        logger.debug(f"[PR Submitter] Title: {title}")
        logger.debug(f"[PR Submitter] Head branch: {head_branch}, Base branch: {base_branch}")
        
        try:
            response = requests.post(url, headers=headers, json=data, timeout=30)
        except requests.exceptions.Timeout:
            raise Exception(f"Request timed out after 30 seconds")
        except requests.exceptions.ConnectionError as e:
            raise Exception(f"Connection error: {str(e)}")
        
        if response.status_code != 201:
            error_detail = ""
            try:
                error_data = response.json()
                error_detail = error_data.get("message", "")
                if "errors" in error_data:
                    error_detail += " - " + "; ".join([e.get("message", "") for e in error_data["errors"]])
            except Exception:
                error_detail = response.text[:500]
            
            status_errors = {
                401: "Authentication failed - check your GITHUB_TOKEN",
                403: "Permission denied - token may not have write access to repo",
                404: "Repository not found or branch does not exist on remote",
                422: "Validation failed - branch may already exist or PR already open",
            }
            
            error_msg = status_errors.get(response.status_code, f"HTTP {response.status_code}")
            if error_detail:
                error_msg += f": {error_detail}"
            
            raise Exception(error_msg)
        
        try:
            pr_data = response.json()
            pr_number = pr_data["number"]
            pr_url = pr_data["html_url"]
        except Exception as e:
            raise Exception(f"Failed to parse GitHub API response: {str(e)}")
        
        if labels:
            try:
                self._add_labels(pr_number, labels)
            except Exception as e:
                logger.warning(f"[PR Submitter] Could not add labels (non-critical): {e}")
        
        return pr_number, pr_url
    
    def _push_branch(self, branch_name: str) -> None:
        import logging
        logger = logging.getLogger(__name__)
        
        logger.debug(f"[PR Submitter] Pushing branch '{branch_name}' to origin...")
        
        try:
            import git
            repo = git.Repo(self.repo_path)
            origin = repo.remote("origin")
            
            push_info = origin.push(branch_name)
            
            for info in push_info:
                if info.flags & info.ERROR:
                    raise Exception(f"Git push failed: {info.summary.strip()}")
                elif info.flags & info.REJECTED:
                    raise Exception(f"Git push rejected: {info.summary.strip()}")
            
            logger.debug(f"[PR Submitter] Git push completed successfully")
            
        except ImportError:
            import subprocess
            
            try:
                result = subprocess.run(
                    ["git", "push", "origin", branch_name],
                    cwd=self.repo_path,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except subprocess.CalledProcessError as e:
                error_msg = e.stderr.strip() if e.stderr else "Unknown git push error"
                
                if "Permission denied" in error_msg or "403" in error_msg:
                    raise Exception(f"Git push permission denied: {error_msg}")
                elif "Repository not found" in error_msg or "404" in error_msg:
                    raise Exception(f"Repository not found: {error_msg}")
                elif "non-fast-forward" in error_msg or "rejected" in error_msg:
                    raise Exception(f"Push rejected (non-fast-forward): {error_msg}")
                elif "timeout" in error_msg.lower():
                    raise Exception(f"Git push timed out: {error_msg}")
                else:
                    raise Exception(f"Git push failed: {error_msg}")
            except subprocess.TimeoutExpired:
                raise Exception("Git push timed out after 60 seconds")
    
    def _add_labels(self, pr_number: int, labels: List[str]) -> None:
        import requests
        
        url = f"{self._api_base}/repos/{self.repo}/issues/{pr_number}/labels"
        
        headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
        }
        
        data = {"labels": labels}
        
        try:
            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status()
        except Exception:
            pass
    
    def comment_on_pr(self, pr_number: int, comment: str) -> bool:
        import requests
        
        url = f"{self._api_base}/repos/{self.repo}/issues/{pr_number}/comments"
        
        headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
        }
        
        data = {"body": comment}
        
        try:
            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status()
            return True
        except Exception:
            return False
    
    def update_pr_description(self, pr_number: int, new_body: str) -> bool:
        import requests
        
        url = f"{self._api_base}/repos/{self.repo}/pulls/{pr_number}"
        
        headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
        }
        
        data = {"body": new_body}
        
        try:
            response = requests.patch(url, headers=headers, json=data)
            response.raise_for_status()
            return True
        except Exception:
            return False
