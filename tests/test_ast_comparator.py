import os
import tempfile
import pytest

from src.ast_diff import ASTComparator, CodeChange, ChangeAnalysis, ChangeType


class TestASTComparator:
    def test_compare_files_no_changes(self, tmp_path):
        old_file = tmp_path / "old.py"
        new_file = tmp_path / "new.py"
        
        content = '''
def hello():
    print("Hello World")
'''
        old_file.write_text(content)
        new_file.write_text(content)
        
        comparator = ASTComparator(str(tmp_path))
        changes = comparator.compare_files(str(old_file), str(new_file))
        
        assert len(changes) == 0
    
    def test_compare_files_added_line(self, tmp_path):
        old_file = tmp_path / "old.py"
        new_file = tmp_path / "new.py"
        
        old_content = "def hello():\n    print('hello')\n"
        new_content = "def hello():\n    print('hello')\n    print('world')\n"
        
        old_file.write_text(old_content)
        new_file.write_text(new_content)
        
        comparator = ASTComparator(str(tmp_path))
        changes = comparator.compare_files(str(old_file), str(new_file))
        
        added_changes = [c for c in changes if c.change_type == ChangeType.ADDED]
        assert len(added_changes) > 0
    
    def test_compare_files_removed_line(self, tmp_path):
        old_file = tmp_path / "old.py"
        new_file = tmp_path / "new.py"
        
        old_content = "def hello():\n    print('hello')\n    print('world')\n"
        new_content = "def hello():\n    print('hello')\n"
        
        old_file.write_text(old_content)
        new_file.write_text(new_content)
        
        comparator = ASTComparator(str(tmp_path))
        changes = comparator.compare_files(str(old_file), str(new_file))
        
        removed_changes = [c for c in changes if c.change_type == ChangeType.REMOVED]
        assert len(removed_changes) > 0
    
    def test_change_analysis_empty(self):
        analysis = ChangeAnalysis()
        assert not analysis.has_changes
        assert analysis.total_changes == 0
    
    def test_code_change_line_range(self):
        change = CodeChange(
            file_path="test.py",
            line_number=10,
            end_line=15,
            change_type=ChangeType.MODIFIED,
        )
        
        assert change.line_range == (10, 15)
    
    def test_python_ast_enrichment(self, tmp_path):
        old_file = tmp_path / "old.py"
        new_file = tmp_path / "new.py"
        
        old_content = '''
class MyClass:
    def method(self):
        x = 1
        return x
'''
        new_content = '''
class MyClass:
    def method(self):
        x = 2
        return x
'''
        
        old_file.write_text(old_content)
        new_file.write_text(new_content)
        
        comparator = ASTComparator(str(tmp_path))
        changes = comparator.compare_files(str(old_file), str(new_file))
        
        assert len(changes) > 0
    
    def test_find_changes_for_error(self):
        analysis = ChangeAnalysis()
        analysis.changes = [
            CodeChange(
                file_path="src/utils.py",
                line_number=42,
                end_line=45,
                change_type=ChangeType.MODIFIED,
            ),
            CodeChange(
                file_path="src/other.py",
                line_number=10,
                end_line=12,
                change_type=ChangeType.ADDED,
            ),
        ]
        
        comparator = ASTComparator()
        relevant = comparator.find_changes_for_error(
            "src/utils.py", 43, analysis
        )
        
        assert len(relevant) >= 1
        assert relevant[0].file_path == "src/utils.py"
    
    def test_get_changed_functions(self):
        analysis = ChangeAnalysis()
        analysis.changes = [
            CodeChange(
                file_path="src/utils.py",
                line_number=10,
                end_line=15,
                change_type=ChangeType.MODIFIED,
                function_name="calculate",
            ),
            CodeChange(
                file_path="src/utils.py",
                line_number=20,
                end_line=25,
                change_type=ChangeType.ADDED,
                function_name="calculate",
            ),
            CodeChange(
                file_path="src/other.py",
                line_number=5,
                end_line=8,
                change_type=ChangeType.MODIFIED,
                function_name="helper",
            ),
        ]
        
        comparator = ASTComparator()
        funcs = comparator.get_changed_functions(analysis)
        
        assert "src/utils.py" in funcs
        assert "calculate" in funcs["src/utils.py"]
        assert "src/other.py" in funcs
        assert "helper" in funcs["src/other.py"]
