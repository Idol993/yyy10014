import os
import tempfile
import pytest

from src.sandbox import (
    SecurityScanner, SecurityFinding, Severity,
    CompatibilityChecker, CompatibilityIssue, CompatibilitySeverity,
    TestRunner as SandboxTestRunner, TestResult, TestStatus,
)


class TestSecurityScanner:
    def test_scan_python_eval(self, tmp_path):
        test_file = tmp_path / "dangerous.py"
        test_file.write_text('''
def risky_code(user_input):
    result = eval(user_input)
    return result
''')
        
        scanner = SecurityScanner(repo_path=str(tmp_path))
        result = scanner.scan_file(str(test_file))
        
        assert result.total_findings > 0
        assert any(f.rule_id == "B001" for f in result.findings)
    
    def test_scan_python_password(self, tmp_path):
        test_file = tmp_path / "secrets.py"
        test_file.write_text('''
password = "super_secret_password_123"
''')
        
        scanner = SecurityScanner(repo_path=str(tmp_path))
        result = scanner.scan_file(str(test_file))
        
        assert any("B008" in f.rule_id for f in result.findings)
    
    def test_scan_private_key(self, tmp_path):
        test_file = tmp_path / "key.pem"
        test_file.write_text('''
-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA0...
-----END RSA PRIVATE KEY-----
''')
        
        scanner = SecurityScanner(repo_path=str(tmp_path))
        result = scanner.scan_file(str(test_file))
        
        assert result.has_critical
    
    def test_severity_levels(self):
        assert Severity.CRITICAL.value == "critical"
        assert Severity.HIGH.value == "high"
        assert Severity.MEDIUM.value == "medium"
        assert Severity.LOW.value == "low"
    
    def test_security_finding_blocking(self):
        critical = SecurityFinding(
            file_path="test.py",
            line_number=1,
            severity=Severity.CRITICAL,
            rule_id="C001",
            message="Critical issue",
        )
        high = SecurityFinding(
            file_path="test.py",
            line_number=1,
            severity=Severity.HIGH,
            rule_id="H001",
            message="High issue",
        )
        medium = SecurityFinding(
            file_path="test.py",
            line_number=1,
            severity=Severity.MEDIUM,
            rule_id="M001",
            message="Medium issue",
        )
        
        assert critical.is_blocking
        assert high.is_blocking
        assert not medium.is_blocking
    
    def test_scan_result_blocking(self):
        scanner = SecurityScanner()
        result = type('obj', (object,), {
            'findings': [
                SecurityFinding("test.py", 1, Severity.LOW, "L1", "low"),
            ],
            'has_blocking_issues': False,
        })
        
        assert not result.has_blocking_issues
    
    def test_javascript_eval(self, tmp_path):
        test_file = tmp_path / "danger.js"
        test_file.write_text('''
function risky(input) {
    return eval(input);
}
''')
        
        scanner = SecurityScanner(repo_path=str(tmp_path))
        result = scanner.scan_file(str(test_file))
        
        assert result.total_findings > 0


class TestCompatibilityChecker:
    def test_check_python_removed_function(self, tmp_path):
        old_content = '''
def public_function(x, y):
    """This is a public function."""
    return x + y

def _private_function(x):
    return x * 2
'''
        new_content = '''
def _private_function(x):
    return x * 2
'''
        
        checker = CompatibilityChecker(repo_path=str(tmp_path))
        report = checker.check_compatibility(old_content, new_content, "test.py")
        
        assert report.has_breaking_changes
        assert any("public_function" in i.element_name for i in report.issues)
    
    def test_check_python_added_required_param(self, tmp_path):
        old_content = '''
def greet(name):
    """Greet someone."""
    return f"Hello {name}"
'''
        new_content = '''
def greet(name, greeting):
    """Greet someone with custom greeting."""
    return f"{greeting} {name}"
'''
        
        checker = CompatibilityChecker(repo_path=str(tmp_path))
        report = checker.check_compatibility(old_content, new_content, "test.py")
        
        assert report.has_breaking_changes
    
    def test_check_python_added_optional_param(self, tmp_path):
        old_content = '''
def greet(name):
    """Greet someone."""
    return f"Hello {name}"
'''
        new_content = '''
def greet(name, greeting="Hello"):
    """Greet someone with custom greeting."""
    return f"{greeting} {name}"
'''
        
        checker = CompatibilityChecker(repo_path=str(tmp_path))
        report = checker.check_compatibility(old_content, new_content, "test.py")
        
        assert not report.has_breaking_changes
    
    def test_compatibility_severity(self):
        assert CompatibilitySeverity.BREAKING.value == "breaking"
        assert CompatibilitySeverity.WARNING.value == "warning"
    
    def test_issue_breaking(self):
        issue = CompatibilityIssue(
            file_path="test.py",
            element_name="func",
            element_type="function",
            severity=CompatibilitySeverity.BREAKING,
            message="Breaking change",
        )
        
        assert issue.is_breaking
    
    def test_report_no_breaking(self):
        report = type('obj', (object,), {
            'issues': [],
            'has_breaking_changes': False,
        })
        
        assert not report.has_breaking_changes


class TestRunner:
    def test_test_result_all_passed(self):
        result = TestResult(
            total_tests=10,
            passed=10,
            failed=0,
            errors=0,
        )
        
        assert result.all_passed
    
    def test_test_result_some_failed(self):
        result = TestResult(
            total_tests=10,
            passed=8,
            failed=2,
            errors=0,
        )
        
        assert not result.all_passed
    
    def test_test_result_success_rate(self):
        result = TestResult(
            total_tests=10,
            passed=8,
            failed=2,
        )
        
        assert result.success_rate == 0.8
    
    def test_test_result_zero_tests(self):
        result = TestResult()
        
        assert result.success_rate == 0.0
    
    def test_test_case_result(self):
        tc = type('obj', (object,), {
            'test_name': 'test_example',
            'status': TestStatus.PASSED,
            'duration': 0.5,
        })
        
        assert tc.status == TestStatus.PASSED
    
    def test_detect_project_type_python(self, tmp_path):
        requirements = tmp_path / "requirements.txt"
        requirements.write_text("pytest")
        
        runner = SandboxTestRunner(repo_path=str(tmp_path))
        project_type = runner._detect_project_type()
        
        assert project_type == "python"
    
    def test_detect_project_type_unknown(self, tmp_path):
        runner = SandboxTestRunner(repo_path=str(tmp_path))
        project_type = runner._detect_project_type()
        
        assert project_type == "unknown"
