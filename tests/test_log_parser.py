import os
import tempfile
import pytest

from src.log_parser import BuildLogParser, BuildError, ParsedBuildLog


class TestBuildLogParser:
    def test_empty_log(self, tmp_path):
        log_file = tmp_path / "empty.log"
        log_file.write_text("")
        
        parser = BuildLogParser(str(log_file))
        result = parser.parse()
        
        assert isinstance(result, ParsedBuildLog)
        assert not result.has_errors
        assert result.error_count == 0
    
    def test_python_traceback(self, tmp_path):
        log_content = """
Traceback (most recent call last):
  File "src/main.py", line 42, in process_data
    result = calculate(items)
  File "src/utils.py", line 128, in calculate
    return sum(values) / len(values)
ZeroDivisionError: division by zero
"""
        log_file = tmp_path / "python_error.log"
        log_file.write_text(log_content)
        
        parser = BuildLogParser(str(log_file))
        result = parser.parse()
        
        assert result.has_errors
        assert any(e.error_type == "ZeroDivisionError" for e in result.errors)
    
    def test_python_syntax_error(self, tmp_path):
        log_content = """
  File "src/broken.py", line 15
    def function(
              ^
SyntaxError: invalid syntax
"""
        log_file = tmp_path / "syntax_error.log"
        log_file.write_text(log_content)
        
        parser = BuildLogParser(str(log_file))
        result = parser.parse()
        
        assert result.has_errors
        assert any(e.error_type == "SyntaxError" for e in result.errors)
    
    def test_detect_build_tool_python(self, tmp_path):
        log_content = """
===== test session starts =====
platform linux -- Python 3.11.0
collected 42 items

tests/test_foo.py .F...
"""
        log_file = tmp_path / "pytest.log"
        log_file.write_text(log_content)
        
        parser = BuildLogParser(str(log_file))
        result = parser.parse()
        
        assert result.build_tool in ("pytest", "pytest2")
    
    def test_build_status_failed(self, tmp_path):
        log_content = "BUILD FAILED with 3 errors"
        log_file = tmp_path / "failed.log"
        log_file.write_text(log_content)
        
        parser = BuildLogParser(str(log_file))
        result = parser.parse()
        
        assert result.build_status == "failed"
    
    def test_build_status_success(self, tmp_path):
        log_content = "BUILD SUCCESS in 2m 30s"
        log_file = tmp_path / "success.log"
        log_file.write_text(log_content)
        
        parser = BuildLogParser(str(log_file))
        result = parser.parse()
        
        assert result.build_status == "success"
    
    def test_get_errors_by_file(self, tmp_path):
        log_content = """
Traceback (most recent call last):
  File "src/utils.py", line 128, in calculate
    return sum(values) / len(values)
ZeroDivisionError: division by zero

Traceback (most recent call last):
  File "src/utils.py", line 200, in process
    return result * 2
TypeError: unsupported operand type
"""
        log_file = tmp_path / "multi_errors.log"
        log_file.write_text(log_content)
        
        parser = BuildLogParser(str(log_file))
        errors_by_file = parser.get_errors_by_file()
        
        assert "src/utils.py" in errors_by_file
        assert len(errors_by_file["src/utils.py"]) >= 2
    
    def test_most_critical_errors(self, tmp_path):
        log_content = """
Traceback (most recent call last):
  File "src/a.py", line 10, in foo
    pass
ValueError: first error

Traceback (most recent call last):
  File "src/b.py", line 20, in bar
    pass
TypeError: second error
"""
        log_file = tmp_path / "critical.log"
        log_file.write_text(log_content)
        
        parser = BuildLogParser(str(log_file))
        top_errors = parser.get_most_critical_errors(top_n=1)
        
        assert len(top_errors) == 1
