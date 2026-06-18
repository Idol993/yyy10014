import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum


class Severity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class SecurityFinding:
    file_path: str
    line_number: int
    severity: Severity
    rule_id: str
    message: str
    details: str = ""
    confidence: str = "high"
    
    @property
    def is_blocking(self) -> bool:
        return self.severity in (Severity.CRITICAL, Severity.HIGH)


@dataclass
class SecurityScanResult:
    findings: List[SecurityFinding] = field(default_factory=list)
    scan_tool: str = ""
    scanned_files: List[str] = field(default_factory=list)
    
    @property
    def has_critical(self) -> bool:
        return any(f.severity == Severity.CRITICAL for f in self.findings)
    
    @property
    def has_high(self) -> bool:
        return any(f.severity == Severity.HIGH for f in self.findings)
    
    @property
    def has_blocking_issues(self) -> bool:
        return any(f.is_blocking for f in self.findings)
    
    @property
    def total_findings(self) -> int:
        return len(self.findings)
    
    def get_findings_by_severity(self, severity: Severity) -> List[SecurityFinding]:
        return [f for f in self.findings if f.severity == severity]


class SecurityScanner:
    def __init__(self, repo_path: str = ".", severity_threshold: str = "medium"):
        self.repo_path = repo_path
        self.severity_threshold = self._parse_severity(severity_threshold)
    
    def _parse_severity(self, severity_str: str) -> Severity:
        severity_map = {
            "critical": Severity.CRITICAL,
            "high": Severity.HIGH,
            "medium": Severity.MEDIUM,
            "low": Severity.LOW,
            "info": Severity.INFO,
        }
        return severity_map.get(severity_str.lower(), Severity.MEDIUM)
    
    def scan_file(self, file_path: str) -> SecurityScanResult:
        result = SecurityScanResult(scan_tool="security_scanner", scanned_files=[file_path])
        
        ext = os.path.splitext(file_path)[1]
        
        if ext == '.py':
            findings = self._scan_python_file(file_path)
        elif ext in ('.js', '.ts', '.jsx', '.tsx'):
            findings = self._scan_javascript_file(file_path)
        elif ext == '.java':
            findings = self._scan_java_file(file_path)
        else:
            findings = self._generic_security_check(file_path)
        
        result.findings = [
            f for f in findings 
            if self._severity_gte(f.severity, self.severity_threshold)
        ]
        
        return result
    
    def scan_changed_files(self, files: List[str]) -> SecurityScanResult:
        result = SecurityScanResult(scan_tool="security_scanner")
        
        for file_path in files:
            full_path = os.path.join(self.repo_path, file_path)
            if os.path.exists(full_path):
                file_result = self.scan_file(full_path)
                result.findings.extend(file_result.findings)
                result.scanned_files.append(file_path)
        
        return result
    
    def _severity_gte(self, severity1: Severity, severity2: Severity) -> bool:
        order = [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
        return order.index(severity1) >= order.index(severity2)
    
    def _scan_python_file(self, file_path: str) -> List[SecurityFinding]:
        findings = []
        
        try:
            bandit_findings = self._run_bandit(file_path)
            findings.extend(bandit_findings)
        except Exception:
            pass
        
        static_findings = self._static_python_check(file_path)
        findings.extend(static_findings)
        
        return findings
    
    def _run_bandit(self, file_path: str) -> List[SecurityFinding]:
        findings = []
        
        try:
            result = subprocess.run(
                ["bandit", "-f", "json", "-r", file_path],
                capture_output=True,
                text=True,
                timeout=60,
            )
            
            if result.returncode != 0 and result.stdout:
                import json
                try:
                    data = json.loads(result.stdout)
                    
                    for issue in data.get("results", []):
                        severity_str = issue.get("issue_severity", "").lower()
                        severity = self._parse_severity(severity_str)
                        
                        finding = SecurityFinding(
                            file_path=issue.get("filename", file_path),
                            line_number=issue.get("line_number", 0),
                            severity=severity,
                            rule_id=issue.get("test_id", ""),
                            message=issue.get("issue_text", ""),
                            details=issue.get("more_info", ""),
                            confidence=issue.get("issue_confidence", "medium"),
                        )
                        findings.append(finding)
                except json.JSONDecodeError:
                    pass
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        
        return findings
    
    def _static_python_check(self, file_path: str) -> List[SecurityFinding]:
        findings = []
        
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
                lines = content.split('\n')
            
            patterns = [
                (r'eval\s*\(', Severity.HIGH, "B001", "Use of eval() detected - potential code injection"),
                (r'exec\s*\(', Severity.HIGH, "B002", "Use of exec() detected - potential code injection"),
                (r'pickle\.loads?\s*\(', Severity.HIGH, "B003", "Use of pickle loads - potential code injection"),
                (r'os\.system\s*\(', Severity.MEDIUM, "B004", "Use of os.system() - consider subprocess with shell=False"),
                (r'subprocess\.call.*shell\s*=\s*True', Severity.HIGH, "B005", "subprocess with shell=True - potential command injection"),
                (r'__import__\s*\(', Severity.MEDIUM, "B006", "Dynamic import with __import__ - review needed"),
                (r'md5|sha1', Severity.LOW, "B007", "Weak hash algorithm detected"),
                (r'password\s*=\s*["\'].*["\']', Severity.MEDIUM, "B008", "Potential hardcoded password"),
                (r'secret\s*=\s*["\'].*["\']', Severity.MEDIUM, "B009", "Potential hardcoded secret"),
                (r'api_?key\s*=\s*["\'].*["\']', Severity.MEDIUM, "B010", "Potential hardcoded API key"),
            ]
            
            for i, line in enumerate(lines, 1):
                for pattern, severity, rule_id, message in patterns:
                    if re.search(pattern, line, re.IGNORECASE):
                        finding = SecurityFinding(
                            file_path=file_path,
                            line_number=i,
                            severity=severity,
                            rule_id=rule_id,
                            message=message,
                            confidence="medium",
                        )
                        findings.append(finding)
            
            findings = self._filter_false_positives(findings, lines)
            
        except Exception:
            pass
        
        return findings
    
    def _filter_false_positives(self, findings: List[SecurityFinding], 
                                lines: List[str]) -> List[SecurityFinding]:
        filtered = []
        
        for finding in findings:
            line_idx = finding.line_number - 1
            if line_idx < len(lines):
                line = lines[line_idx].strip()
                if line.startswith('#'):
                    continue
                if '# noqa' in line or '# nosec' in line:
                    continue
            
            filtered.append(finding)
        
        return filtered
    
    def _scan_javascript_file(self, file_path: str) -> List[SecurityFinding]:
        findings = []
        
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
                lines = content.split('\n')
            
            patterns = [
                (r'eval\s*\(', Severity.HIGH, "JS001", "Use of eval() detected - potential code injection"),
                (r'innerHTML\s*=', Severity.MEDIUM, "JS002", "Setting innerHTML - potential XSS vulnerability"),
                (r'document\.write\s*\(', Severity.MEDIUM, "JS003", "Use of document.write() - potential XSS"),
                (r'new\s+Function\s*\(', Severity.HIGH, "JS004", "Use of Function constructor - potential code injection"),
                (r'window\.location\s*=.*\$', Severity.MEDIUM, "JS005", "Potential open redirect"),
                (r'localStorage\.setItem.*password', Severity.HIGH, "JS006", "Storing password in localStorage"),
                (r'postMessage.*targetOrigin\s*:\s*["\']\*', Severity.MEDIUM, "JS007", "postMessage with wildcard targetOrigin"),
            ]
            
            for i, line in enumerate(lines, 1):
                for pattern, severity, rule_id, message in patterns:
                    if re.search(pattern, line):
                        finding = SecurityFinding(
                            file_path=file_path,
                            line_number=i,
                            severity=severity,
                            rule_id=rule_id,
                            message=message,
                            confidence="medium",
                        )
                        findings.append(finding)
            
        except Exception:
            pass
        
        return findings
    
    def _scan_java_file(self, file_path: str) -> List[SecurityFinding]:
        findings = []
        
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
                lines = content.split('\n')
            
            patterns = [
                (r'Runtime\.getRuntime\(\)\.exec', Severity.HIGH, "JAVA001", "Runtime.exec() - potential command injection"),
                (r'new\s+ProcessBuilder', Severity.MEDIUM, "JAVA002", "ProcessBuilder - review for command injection"),
                (r'SQL_INJECTION|Statement\s+\w+\s*=\s*.*\+', Severity.HIGH, "JAVA003", "Potential SQL injection"),
                (r'MessageDigest\.getInstance\("MD5"\)', Severity.LOW, "JAVA004", "Weak hash algorithm MD5"),
                (r'password\s*=\s*".*"', Severity.MEDIUM, "JAVA005", "Potential hardcoded password"),
            ]
            
            for i, line in enumerate(lines, 1):
                for pattern, severity, rule_id, message in patterns:
                    if re.search(pattern, line):
                        finding = SecurityFinding(
                            file_path=file_path,
                            line_number=i,
                            severity=severity,
                            rule_id=rule_id,
                            message=message,
                            confidence="medium",
                        )
                        findings.append(finding)
            
        except Exception:
            pass
        
        return findings
    
    def _generic_security_check(self, file_path: str) -> List[SecurityFinding]:
        findings = []
        
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
                lines = content.split('\n')
            
            patterns = [
                (r'-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----', Severity.CRITICAL, "GEN001", "Private key detected in source code"),
                (r'AKIA[0-9A-Z]{16}', Severity.CRITICAL, "GEN002", "Potential AWS access key"),
                (r'ssh-rsa AAAA', Severity.HIGH, "GEN003", "SSH public key in source"),
                (r'gh[pousr]_[A-Za-z0-9_]+', Severity.HIGH, "GEN004", "Potential GitHub token"),
            ]
            
            for i, line in enumerate(lines, 1):
                for pattern, severity, rule_id, message in patterns:
                    if re.search(pattern, line):
                        finding = SecurityFinding(
                            file_path=file_path,
                            line_number=i,
                            severity=severity,
                            rule_id=rule_id,
                            message=message,
                            confidence="high",
                        )
                        findings.append(finding)
            
        except Exception:
            pass
        
        return findings
    
    def run_semgrep_scan(self, files: Optional[List[str]] = None) -> SecurityScanResult:
        result = SecurityScanResult(scan_tool="semgrep")
        
        try:
            cmd = ["semgrep", "--config", "auto", "--json"]
            
            if files:
                cmd.extend(files)
            else:
                cmd.append(self.repo_path)
            
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=self.repo_path,
            )
            
            if proc.stdout:
                import json
                try:
                    data = json.loads(proc.stdout)
                    
                    for issue in data.get("results", []):
                        severity_str = issue.get("extra", {}).get("severity", "").lower()
                        severity_map = {
                            "error": Severity.HIGH,
                            "warning": Severity.MEDIUM,
                            "info": Severity.LOW,
                        }
                        severity = severity_map.get(severity_str, Severity.MEDIUM)
                        
                        finding = SecurityFinding(
                            file_path=issue.get("path", ""),
                            line_number=issue.get("start", {}).get("line", 0),
                            severity=severity,
                            rule_id=issue.get("check_id", ""),
                            message=issue.get("extra", {}).get("message", ""),
                            details=issue.get("extra", {}).get("metadata", {}).get("description", ""),
                        )
                        result.findings.append(finding)
                        
                except json.JSONDecodeError:
                    pass
                    
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        
        return result
