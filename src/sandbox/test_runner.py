import os
import re
import subprocess
import tempfile
import shutil
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from enum import Enum
import time


class TestStatus(Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class TestCaseResult:
    test_name: str
    status: TestStatus
    duration: float = 0.0
    output: str = ""
    error_message: str = ""


@dataclass
class TestResult:
    test_cases: List[TestCaseResult] = field(default_factory=list)
    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    total_duration: float = 0.0
    output: str = ""
    
    @property
    def all_passed(self) -> bool:
        return self.failed == 0 and self.errors == 0
    
    @property
    def success_rate(self) -> float:
        if self.total_tests == 0:
            return 0.0
        return self.passed / self.total_tests


class TestRunner:
    def __init__(self, repo_path: str = ".", timeout: int = 300):
        self.repo_path = repo_path
        self.timeout = timeout
    
    def run_tests(self, test_files: Optional[List[str]] = None) -> TestResult:
        result = TestResult()
        
        project_type = self._detect_project_type()
        
        if project_type == "python":
            result = self._run_python_tests(test_files)
        elif project_type == "javascript":
            result = self._run_javascript_tests(test_files)
        elif project_type == "java":
            result = self._run_java_tests(test_files)
        elif project_type == "go":
            result = self._run_go_tests(test_files)
        else:
            result = self._run_generic_tests(test_files)
        
        return result
    
    def _detect_project_type(self) -> str:
        markers = {
            "python": ["setup.py", "pyproject.toml", "requirements.txt", "pytest.ini"],
            "javascript": ["package.json", "jest.config.js", "mocha.opts"],
            "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
            "go": ["go.mod", "go.sum"],
            "rust": ["Cargo.toml"],
        }
        
        for project_type, marker_files in markers.items():
            for marker in marker_files:
                if os.path.exists(os.path.join(self.repo_path, marker)):
                    return project_type
        
        return "unknown"
    
    def _run_python_tests(self, test_files: Optional[List[str]] = None) -> TestResult:
        result = TestResult()
        
        try:
            cmd = ["python", "-m", "pytest", "-v", "--tb=short"]
            
            if test_files:
                cmd.extend(test_files)
            
            start_time = time.time()
            
            proc = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            
            result.total_duration = time.time() - start_time
            result.output = proc.stdout + proc.stderr
            
            try:
                result = self._parse_pytest_output(proc.stdout, result)
            except Exception as e:
                result.errors = 1
                result.output += f"\nTest output parsing error: {e}"
            
        except subprocess.TimeoutExpired:
            result.errors = 1
            result.output = f"Test execution timed out after {self.timeout} seconds"
        except FileNotFoundError:
            result = self._run_python_unittest(test_files)
        except Exception as e:
            result.errors = 1
            result.output = f"Test execution error: {e}"
        
        return result
    
    def _parse_pytest_output(self, output: str, result: TestResult) -> TestResult:
        lines = output.split('\n')
        
        for line in lines:
            line = line.strip()
            
            if 'PASSED' in line:
                test_name = line.split('PASSED')[0].strip()
                result.test_cases.append(TestCaseResult(
                    test_name=test_name,
                    status=TestStatus.PASSED,
                ))
                result.passed += 1
                result.total_tests += 1
            elif 'FAILED' in line:
                test_name = line.split('FAILED')[0].strip()
                result.test_cases.append(TestCaseResult(
                    test_name=test_name,
                    status=TestStatus.FAILED,
                ))
                result.failed += 1
                result.total_tests += 1
            elif 'SKIPPED' in line:
                test_name = line.split('SKIPPED')[0].strip()
                result.test_cases.append(TestCaseResult(
                    test_name=test_name,
                    status=TestStatus.SKIPPED,
                ))
                result.skipped += 1
                result.total_tests += 1
            elif 'ERROR' in line and '::' in line:
                test_name = line.split('ERROR')[0].strip()
                result.test_cases.append(TestCaseResult(
                    test_name=test_name,
                    status=TestStatus.ERROR,
                ))
                result.errors += 1
                result.total_tests += 1
        
        summary_match = re.search(r'(\d+)\s+passed', output)
        if summary_match:
            result.passed = int(summary_match.group(1))
        
        summary_match = re.search(r'(\d+)\s+failed', output)
        if summary_match:
            result.failed = int(summary_match.group(1))
        
        summary_match = re.search(r'(\d+)\s+skipped', output)
        if summary_match:
            result.skipped = int(summary_match.group(1))
        
        summary_match = re.search(r'(\d+)\s+error', output)
        if summary_match:
            result.errors = int(summary_match.group(1))
        
        result.total_tests = result.passed + result.failed + result.skipped + result.errors
        
        return result
    
    def _run_python_unittest(self, test_files: Optional[List[str]] = None) -> TestResult:
        result = TestResult()
        
        try:
            cmd = ["python", "-m", "unittest", "discover", "-v"]
            
            if test_files:
                cmd = ["python", "-m", "unittest"] + test_files
            
            proc = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            
            result.output = proc.stdout + proc.stderr
            
            if proc.returncode != 0:
                result.failed = len(re.findall(r'FAIL:', result.output))
                result.errors = len(re.findall(r'ERROR:', result.output))
            
            result.total_tests = len(re.findall(r'^test_', result.output, re.MULTILINE))
            if result.total_tests == 0:
                result.total_tests = result.failed + result.errors
            result.passed = max(0, result.total_tests - result.failed - result.errors)
            
        except Exception as e:
            result.errors = 1
            result.output = str(e)
        
        return result
    
    def _run_javascript_tests(self, test_files: Optional[List[str]] = None) -> TestResult:
        result = TestResult()
        
        try:
            if os.path.exists(os.path.join(self.repo_path, "package.json")):
                with open(os.path.join(self.repo_path, "package.json"), "r") as f:
                    import json
                    package = json.load(f)
                
                scripts = package.get("scripts", {})
                
                test_script = None
                if "test" in scripts:
                    test_script = "test"
                elif "jest" in scripts:
                    test_script = "jest"
                
                if test_script:
                    cmd = ["npm", "test", "--", "--verbose"] if test_files else ["npm", "test"]
                    
                    start_time = time.time()
                    proc = subprocess.run(
                        cmd,
                        cwd=self.repo_path,
                        capture_output=True,
                        text=True,
                        timeout=self.timeout,
                    )
                    
                    result.total_duration = time.time() - start_time
                    result.output = proc.stdout + proc.stderr
                    
                    pass_match = re.search(r'(\d+)\s+pass', result.output)
                    if pass_match:
                        result.passed = int(pass_match.group(1))
                    
                    fail_match = re.search(r'(\d+)\s+fail', result.output)
                    if fail_match:
                        result.failed = int(fail_match.group(1))
                    
                    result.total_tests = result.passed + result.failed
        
        except Exception as e:
            result.errors = 1
            result.output = str(e)
        
        return result
    
    def _run_java_tests(self, test_files: Optional[List[str]] = None) -> TestResult:
        result = TestResult()
        
        try:
            if os.path.exists(os.path.join(self.repo_path, "pom.xml")):
                cmd = ["mvn", "test"]
                
                start_time = time.time()
                proc = subprocess.run(
                    cmd,
                    cwd=self.repo_path,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )
                
                result.total_duration = time.time() - start_time
                result.output = proc.stdout + proc.stderr
                
                run_match = re.search(r'Tests run: (\d+)', result.output)
                if run_match:
                    result.total_tests = int(run_match.group(1))
                
                fail_match = re.search(r'Failures: (\d+)', result.output)
                if fail_match:
                    result.failed = int(fail_match.group(1))
                
                err_match = re.search(r'Errors: (\d+)', result.output)
                if err_match:
                    result.errors = int(err_match.group(1))
                
                result.passed = result.total_tests - result.failed - result.errors
        
        except Exception as e:
            result.errors = 1
            result.output = str(e)
        
        return result
    
    def _run_go_tests(self, test_files: Optional[List[str]] = None) -> TestResult:
        result = TestResult()
        
        try:
            cmd = ["go", "test", "-v", "./..."]
            
            if test_files:
                cmd = ["go", "test", "-v"] + test_files
            
            start_time = time.time()
            proc = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            
            result.total_duration = time.time() - start_time
            result.output = proc.stdout + proc.stderr
            
            for line in result.output.split('\n'):
                if '--- PASS:' in line:
                    result.passed += 1
                    result.total_tests += 1
                elif '--- FAIL:' in line:
                    result.failed += 1
                    result.total_tests += 1
                elif '--- SKIP:' in line:
                    result.skipped += 1
                    result.total_tests += 1
        
        except Exception as e:
            result.errors = 1
            result.output = str(e)
        
        return result
    
    def _run_generic_tests(self, test_files: Optional[List[str]] = None) -> TestResult:
        result = TestResult()
        result.output = "No test runner detected for this project type"
        return result
    
    def run_relevant_tests(self, changed_files: List[str]) -> TestResult:
        test_files = self._find_related_tests(changed_files)
        
        if test_files:
            return self.run_tests(test_files)
        else:
            return self.run_tests()
    
    def _find_related_tests(self, changed_files: List[str]) -> List[str]:
        test_files = []
        
        test_patterns = {
            '.py': [r'test_{base}.py', r'{base}_test.py'],
            '.js': [r'{base}.test.js', r'{base}.spec.js', r'{base}.test.ts', r'{base}.spec.ts'],
            '.java': [r'{base}Test.java'],
            '.go': [r'{base}_test.go'],
        }
        
        for file_path in changed_files:
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            ext = os.path.splitext(file_path)[1]
            dir_name = os.path.dirname(file_path)
            
            patterns = test_patterns.get(ext, [])
            
            for pattern in patterns:
                test_name = pattern.format(base=base_name)
                test_path = os.path.join(dir_name, test_name)
                
                if os.path.exists(os.path.join(self.repo_path, test_path)):
                    test_files.append(test_path)
                
                test_dir = os.path.join(os.path.dirname(dir_name), "tests")
                if os.path.exists(os.path.join(self.repo_path, test_dir)):
                    test_path = os.path.join(test_dir, test_name)
                    if os.path.exists(os.path.join(self.repo_path, test_path)):
                        test_files.append(test_path)
        
        return list(set(test_files))
    
    def run_in_isolation(self, patch_file: str) -> TestResult:
        temp_dir = tempfile.mkdtemp(prefix="cicd_fix_sandbox_")
        
        try:
            self._copy_repo_to_temp(temp_dir)
            self._apply_patch(temp_dir, patch_file)
            
            original_repo_path = self.repo_path
            self.repo_path = temp_dir
            
            try:
                result = self.run_tests()
            finally:
                self.repo_path = original_repo_path
            
            return result
            
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    
    def _copy_repo_to_temp(self, temp_dir: str) -> None:
        exclude_dirs = {'.git', 'node_modules', '__pycache__', 'venv', '.venv', 'build', 'dist'}
        
        for item in os.listdir(self.repo_path):
            src = os.path.join(self.repo_path, item)
            dst = os.path.join(temp_dir, item)
            
            if item in exclude_dirs:
                continue
            
            if os.path.isdir(src):
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns('*.pyc', '*.pyo'))
            else:
                shutil.copy2(src, dst)
    
    def _apply_patch(self, temp_dir: str, patch_file: str) -> None:
        try:
            subprocess.run(
                ["git", "apply", patch_file],
                cwd=temp_dir,
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:
            pass
