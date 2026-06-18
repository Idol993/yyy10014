import re
import os
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from pathlib import Path


@dataclass
class BuildError:
    error_type: str
    message: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    column_number: Optional[int] = None
    full_context: str = ""
    error_code: Optional[str] = None
    severity: str = "error"


@dataclass
class ParsedBuildLog:
    errors: List[BuildError] = field(default_factory=list)
    warnings: List[BuildError] = field(default_factory=list)
    build_tool: Optional[str] = None
    build_status: str = "unknown"
    raw_log: str = ""
    
    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0
    
    @property
    def error_count(self) -> int:
        return len(self.errors)


class BuildLogParser:
    def __init__(self, log_path: str):
        self.log_path = log_path
        self._raw_log = ""
        self._parsed_log: Optional[ParsedBuildLog] = None
    
    def parse(self) -> ParsedBuildLog:
        if self._parsed_log:
            return self._parsed_log
        
        self._read_log()
        self._parsed_log = ParsedBuildLog(raw_log=self._raw_log)
        
        self._detect_build_tool()
        self._extract_errors()
        self._extract_warnings()
        self._determine_build_status()
        
        return self._parsed_log
    
    def _read_log(self) -> None:
        if os.path.exists(self.log_path):
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                self._raw_log = f.read()
        else:
            self._raw_log = ""
    
    def _detect_build_tool(self) -> None:
        if not self._parsed_log:
            return
        
        patterns = {
            "maven": r"Apache Maven|mvn ",
            "gradle": r"Gradle |Task :",
            "npm": r"npm ERR!|npm run|npm test",
            "yarn": r"yarn run|yarn test",
            "webpack": r"webpack compiled|ERROR in",
            "pytest": r"===== test session starts|FAILED \[",
            "pytest2": r"PASSED|FAILED|ERROR",
            "gcc": r"gcc:|g\+\+:|error:.*\.c:|error:.*\.cpp:",
            "go": r"#.*\.go|go build|cannot find",
            "rust": r"^error\[|^warning\[|compiling.*\.rs",
            "python": r"Traceback \(most recent call last\)|SyntaxError:|ModuleNotFoundError:",
            "java": r"error:.*\.java:|BUILD FAILURE|BUILD FAILED",
        }
        
        for tool, pattern in patterns.items():
            if re.search(pattern, self._raw_log, re.MULTILINE | re.IGNORECASE):
                self._parsed_log.build_tool = tool
                return
        
        self._parsed_log.build_tool = "unknown"
    
    def _extract_errors(self) -> None:
        if not self._parsed_log:
            return
        
        error_patterns = [
            self._python_error_patterns,
            self._javascript_error_patterns,
            self._java_error_patterns,
            self._c_cpp_error_patterns,
            self._go_error_patterns,
            self._rust_error_patterns,
            self._generic_error_patterns,
        ]
        
        for pattern_func in error_patterns:
            errors = pattern_func()
            for error in errors:
                if not self._is_duplicate_error(error):
                    self._parsed_log.errors.append(error)
        
        self._parsed_log.errors.sort(key=lambda e: (e.file_path or "", e.line_number or 0))
    
    def _extract_warnings(self) -> None:
        if not self._parsed_log:
            return
        
        warning_patterns = [
            (r"(?im)^(.*?\.(py|js|ts|java|c|cpp|h|go|rs)):(\d+):(\d+):\s*warning(?:\s*\[[^\]]*\])?:\s*(.+)$", "warning"),
            (r"(?im)^warning(?:\s*\[[^\]]*\])?:\s*(.+?)\n\s+-->\s+(.*?\.\w+):(\d+):(\d+)", "rust_warning"),
        ]
        
        for pattern, wtype in warning_patterns:
            for match in re.finditer(pattern, self._raw_log):
                if wtype == "warning":
                    file_path = match.group(1)
                    line = int(match.group(3))
                    col = int(match.group(4))
                    message = match.group(5)
                else:
                    message = match.group(1)
                    file_path = match.group(2)
                    line = int(match.group(3))
                    col = int(match.group(4))
                
                warning = BuildError(
                    error_type="warning",
                    message=message.strip(),
                    file_path=file_path.strip(),
                    line_number=line,
                    column_number=col,
                    severity="warning",
                )
                self._parsed_log.warnings.append(warning)
    
    def _python_error_patterns(self) -> List[BuildError]:
        errors = []
        
        traceback_pattern = r"""
            Traceback\s\(most\srecent\scall\slast\):\n
            (?:\s+File\s"([^"]+)",\sline\s(\d+),\sin\s.+\n
            (?:\s+.+\n)*?)+
            ([A-Z][\w.]+):\s(.+)
        """
        
        for match in re.finditer(traceback_pattern, self._raw_log, re.VERBOSE):
            file_path = match.group(1)
            line_number = int(match.group(2))
            error_type = match.group(3)
            message = match.group(4)
            
            context = self._extract_context(file_path, line_number, lines_before=5, lines_after=5)
            
            error = BuildError(
                error_type=error_type,
                message=message.strip(),
                file_path=file_path,
                line_number=line_number,
                full_context=context,
                severity="error",
            )
            errors.append(error)
        
        syntax_error_pattern = r'File\s"([^"]+)",\sline\s(\d+)\n\s*.+\n\s*\^\n\s*SyntaxError:\s*(.+)'
        for match in re.finditer(syntax_error_pattern, self._raw_log):
            file_path = match.group(1)
            line_number = int(match.group(2))
            message = match.group(3)
            
            error = BuildError(
                error_type="SyntaxError",
                message=message.strip(),
                file_path=file_path,
                line_number=line_number,
                severity="error",
            )
            errors.append(error)
        
        return errors
    
    def _javascript_error_patterns(self) -> List[BuildError]:
        errors = []
        
        patterns = [
            (r"ERROR in\s+(.+?)\n\s*(.+?)\n\s*\((\d+),(\d+)\):\s*error\s+(TS\d+):\s*(.+)", "typescript"),
            (r"Module not found:\s*(?:Error:\s*)?Can't resolve '(.+?)' in '(.+?)'", "module_not_found"),
            (r"(.+?\.(?:js|ts|jsx|tsx))\((\d+),(\d+)\):\s*error\s+(\w+):\s*(.+)", "generic_js"),
        ]
        
        for pattern, etype in patterns:
            for match in re.finditer(pattern, self._raw_log):
                if etype == "typescript":
                    file_path = match.group(1)
                    msg = match.group(2)
                    line = int(match.group(3))
                    col = int(match.group(4))
                    code = match.group(5)
                    message = match.group(6)
                elif etype == "module_not_found":
                    module_name = match.group(1)
                    file_path = match.group(2)
                    line = None
                    col = None
                    code = None
                    message = f"Module not found: {module_name}"
                else:
                    file_path = match.group(1)
                    line = int(match.group(2))
                    col = int(match.group(3))
                    code = match.group(4)
                    message = match.group(5)
                
                error = BuildError(
                    error_type=etype,
                    message=message.strip(),
                    file_path=file_path,
                    line_number=line,
                    column_number=col,
                    error_code=code,
                    severity="error",
                )
                errors.append(error)
        
        return errors
    
    def _java_error_patterns(self) -> List[BuildError]:
        errors = []
        
        pattern = r"(.+\.java):(\d+):\s*(?:error|warning):\s*(.+)"
        for match in re.finditer(pattern, self._raw_log):
            file_path = match.group(1)
            line = int(match.group(2))
            message = match.group(3)
            
            error = BuildError(
                error_type="compilation_error",
                message=message.strip(),
                file_path=file_path,
                line_number=line,
                severity="error",
            )
            errors.append(error)
        
        return errors
    
    def _c_cpp_error_patterns(self) -> List[BuildError]:
        errors = []
        
        pattern = r"(.+\.(?:c|cpp|cc|cxx|h|hpp)):(\d+):(\d+):\s*(error|warning)(?:\s*\[[^\]]*\])?:\s*(.+)"
        for match in re.finditer(pattern, self._raw_log):
            file_path = match.group(1)
            line = int(match.group(2))
            col = int(match.group(3))
            severity = match.group(4)
            message = match.group(5)
            
            error = BuildError(
                error_type="compilation_error",
                message=message.strip(),
                file_path=file_path,
                line_number=line,
                column_number=col,
                severity=severity,
            )
            errors.append(error)
        
        return errors
    
    def _go_error_patterns(self) -> List[BuildError]:
        errors = []
        
        pattern = r"(.+\.go):(\d+):(\d+):\s*(.+)"
        for match in re.finditer(pattern, self._raw_log):
            file_path = match.group(1)
            line = int(match.group(2))
            col = int(match.group(3))
            message = match.group(4)
            
            if message.strip().startswith("warning"):
                severity = "warning"
            else:
                severity = "error"
            
            error = BuildError(
                error_type="compilation_error",
                message=message.strip(),
                file_path=file_path,
                line_number=line,
                column_number=col,
                severity=severity,
            )
            errors.append(error)
        
        return errors
    
    def _rust_error_patterns(self) -> List[BuildError]:
        errors = []
        
        pattern = r"^error\[(E\d+)\]:\s*(.+?)\n\s+-->\s+(.+\.rs):(\d+):(\d+)"
        for match in re.finditer(pattern, self._raw_log, re.MULTILINE):
            code = match.group(1)
            message = match.group(2)
            file_path = match.group(3)
            line = int(match.group(4))
            col = int(match.group(5))
            
            error = BuildError(
                error_type="compilation_error",
                message=message.strip(),
                file_path=file_path,
                line_number=line,
                column_number=col,
                error_code=code,
                severity="error",
            )
            errors.append(error)
        
        return errors
    
    def _generic_error_patterns(self) -> List[BuildError]:
        errors = []
        
        pattern = r"^\s*(.+?\.\w+):(\d+)\s*[-:]\s*(.+?Error):?\s*(.*)$"
        for match in re.finditer(pattern, self._raw_log, re.MULTILINE):
            file_path = match.group(1)
            line = int(match.group(2))
            error_type = match.group(3)
            message = match.group(4)
            
            error = BuildError(
                error_type=error_type,
                message=message.strip(),
                file_path=file_path,
                line_number=line,
                severity="error",
            )
            errors.append(error)
        
        return errors
    
    def _extract_context(self, file_path: Optional[str], line_number: Optional[int], 
                         lines_before: int = 5, lines_after: int = 5) -> str:
        if not file_path or not line_number:
            return ""
        
        try:
            if not os.path.isabs(file_path):
                file_path = os.path.join(os.getcwd(), file_path)
            
            if not os.path.exists(file_path):
                return ""
            
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            
            start = max(0, line_number - lines_before - 1)
            end = min(len(lines), line_number + lines_after)
            
            context_lines = []
            for i in range(start, end):
                prefix = ">>>" if i + 1 == line_number else "   "
                context_lines.append(f"{prefix} {i+1:4d}: {lines[i].rstrip()}")
            
            return "\n".join(context_lines)
        except Exception:
            return ""
    
    def _is_duplicate_error(self, new_error: BuildError) -> bool:
        if not self._parsed_log:
            return False
        
        for existing in self._parsed_log.errors:
            if (existing.file_path == new_error.file_path and
                existing.line_number == new_error.line_number and
                existing.error_type == new_error.error_type):
                return True
        return False
    
    def _determine_build_status(self) -> None:
        if not self._parsed_log:
            return
        
        success_patterns = [
            r"BUILD SUCCESS",
            r"BUILD SUCCESSFUL",
            r"Compiled successfully",
            r"Tests passed",
            r"All tests passed",
            r"Build succeeded",
            r"===== \d+ passed in",
            r"exit code 0",
        ]
        
        failure_patterns = [
            r"BUILD FAILURE",
            r"BUILD FAILED",
            r"Compilation failed",
            r"Tests failed",
            r"Build failed",
            r"FAILED \[",
            r"Error: Build failed",
            r"exit code [1-9]",
        ]
        
        for pattern in failure_patterns:
            if re.search(pattern, self._raw_log, re.IGNORECASE):
                self._parsed_log.build_status = "failed"
                return
        
        for pattern in success_patterns:
            if re.search(pattern, self._raw_log, re.IGNORECASE):
                self._parsed_log.build_status = "success"
                return
        
        if self._parsed_log.errors:
            self._parsed_log.build_status = "failed"
        else:
            self._parsed_log.build_status = "unknown"
    
    def get_errors_by_file(self) -> Dict[str, List[BuildError]]:
        if not self._parsed_log:
            self.parse()
        
        errors_by_file: Dict[str, List[BuildError]] = {}
        for error in self._parsed_log.errors:
            if error.file_path:
                if error.file_path not in errors_by_file:
                    errors_by_file[error.file_path] = []
                errors_by_file[error.file_path].append(error)
        
        return errors_by_file
    
    def get_most_critical_errors(self, top_n: int = 5) -> List[BuildError]:
        if not self._parsed_log:
            self.parse()
        
        ranked = sorted(
            self._parsed_log.errors,
            key=lambda e: (e.file_path is None, e.line_number is None),
        )
        
        return ranked[:top_n]
