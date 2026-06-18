import os
import re
import ast
import difflib
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple, Set
from pathlib import Path
from enum import Enum


class ChangeType(Enum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    UNCHANGED = "unchanged"


@dataclass
class CodeChange:
    file_path: str
    line_number: int
    end_line: int
    change_type: ChangeType
    old_code: str = ""
    new_code: str = ""
    ast_node_type: Optional[str] = None
    function_name: Optional[str] = None
    class_name: Optional[str] = None
    
    @property
    def line_range(self) -> Tuple[int, int]:
        return (self.line_number, self.end_line)


@dataclass
class ChangeAnalysis:
    changes: List[CodeChange] = field(default_factory=list)
    modified_files: List[str] = field(default_factory=list)
    added_files: List[str] = field(default_factory=list)
    removed_files: List[str] = field(default_factory=list)
    
    @property
    def total_changes(self) -> int:
        return len(self.changes)
    
    @property
    def has_changes(self) -> bool:
        return len(self.changes) > 0


class ASTComparator:
    def __init__(self, repo_path: str = "."):
        self.repo_path = repo_path
    
    def compare_commits(self, old_sha: str, new_sha: str) -> ChangeAnalysis:
        analysis = ChangeAnalysis()
        
        try:
            import git
            repo = git.Repo(self.repo_path)
            
            old_commit = repo.commit(old_sha)
            new_commit = repo.commit(new_sha)
            
            diff = old_commit.diff(new_commit)
            
            for diff_item in diff.iter_change_type('M'):
                file_path = diff_item.a_path
                analysis.modified_files.append(file_path)
                changes = self._compare_file_contents(
                    diff_item.a_blob.data_stream.read().decode('utf-8', errors='replace'),
                    diff_item.b_blob.data_stream.read().decode('utf-8', errors='replace'),
                    file_path
                )
                analysis.changes.extend(changes)
            
            for diff_item in diff.iter_change_type('A'):
                file_path = diff_item.b_path
                analysis.added_files.append(file_path)
            
            for diff_item in diff.iter_change_type('D'):
                file_path = diff_item.a_path
                analysis.removed_files.append(file_path)
                
        except ImportError:
            analysis = self._compare_with_git_cli(old_sha, new_sha)
        except Exception as e:
            print(f"Error comparing commits: {e}")
        
        return analysis
    
    def _compare_with_git_cli(self, old_sha: str, new_sha: str) -> ChangeAnalysis:
        import subprocess
        
        analysis = ChangeAnalysis()
        
        try:
            result = subprocess.run(
                ["git", "diff", "--name-status", f"{old_sha}..{new_sha}"],
                cwd=self.repo_path,
                capture_output=True,
                text=True
            )
            
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                status, file_path = line.split("\t", 1)
                if status.startswith("M"):
                    analysis.modified_files.append(file_path)
                elif status.startswith("A"):
                    analysis.added_files.append(file_path)
                elif status.startswith("D"):
                    analysis.removed_files.append(file_path)
            
            for file_path in analysis.modified_files:
                diff_result = subprocess.run(
                    ["git", "diff", f"{old_sha}:{file_path}", f"{new_sha}:{file_path}"],
                    cwd=self.repo_path,
                    capture_output=True,
                    text=True
                )
                
                old_content_result = subprocess.run(
                    ["git", "show", f"{old_sha}:{file_path}"],
                    cwd=self.repo_path,
                    capture_output=True,
                    text=True
                )
                
                new_content_result = subprocess.run(
                    ["git", "show", f"{new_sha}:{file_path}"],
                    cwd=self.repo_path,
                    capture_output=True,
                    text=True
                )
                
                changes = self._compare_file_contents(
                    old_content_result.stdout,
                    new_content_result.stdout,
                    file_path
                )
                analysis.changes.extend(changes)
                
        except Exception as e:
            print(f"Error with git CLI: {e}")
        
        return analysis
    
    def compare_files(self, old_file: str, new_file: str) -> List[CodeChange]:
        old_content = self._read_file(old_file)
        new_content = self._read_file(new_file)
        
        return self._compare_file_contents(old_content, new_content, new_file)
    
    def _read_file(self, file_path: str) -> str:
        try:
            full_path = os.path.join(self.repo_path, file_path) if not os.path.isabs(file_path) else file_path
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return ""
    
    def _compare_file_contents(self, old_content: str, new_content: str, 
                               file_path: str) -> List[CodeChange]:
        changes = []
        
        old_lines = old_content.splitlines()
        new_lines = new_content.splitlines()
        
        diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
        
        if not diff:
            return changes
        
        old_line_num = 0
        new_line_num = 0
        
        for line in diff:
            if line.startswith("@@"):
                match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
                if match:
                    old_line_num = int(match.group(1)) - 1
                    new_line_num = int(match.group(3)) - 1
            elif line.startswith("+") and not line.startswith("+++"):
                new_line_num += 1
                changes.append(CodeChange(
                    file_path=file_path,
                    line_number=new_line_num,
                    end_line=new_line_num,
                    change_type=ChangeType.ADDED,
                    new_code=line[1:],
                ))
            elif line.startswith("-") and not line.startswith("---"):
                old_line_num += 1
                changes.append(CodeChange(
                    file_path=file_path,
                    line_number=old_line_num,
                    end_line=old_line_num,
                    change_type=ChangeType.REMOVED,
                    old_code=line[1:],
                ))
            else:
                old_line_num += 1
                new_line_num += 1
        
        changes = self._merge_adjacent_changes(changes)
        self._enrich_with_ast_info(changes, new_content, old_content)
        
        return changes
    
    def _merge_adjacent_changes(self, changes: List[CodeChange]) -> List[CodeChange]:
        if not changes:
            return []
        
        merged = []
        current = changes[0]
        
        for change in changes[1:]:
            if (change.change_type == current.change_type and
                change.file_path == current.file_path and
                change.line_number == current.end_line + 1):
                
                current.end_line = change.line_number
                if change.change_type == ChangeType.ADDED:
                    current.new_code += "\n" + change.new_code
                else:
                    current.old_code += "\n" + change.old_code
            else:
                merged.append(current)
                current = change
        
        merged.append(current)
        return merged
    
    def _enrich_with_ast_info(self, changes: List[CodeChange], 
                              new_content: str, old_content: str) -> None:
        file_ext = os.path.splitext(changes[0].file_path if changes else "")[1]
        
        if file_ext == ".py":
            self._enrich_python_ast(changes, new_content, old_content)
        elif file_ext in (".js", ".ts", ".jsx", ".tsx"):
            self._enrich_javascript_ast(changes, new_content, old_content)
        elif file_ext == ".java":
            self._enrich_java_ast(changes, new_content, old_content)
    
    def _enrich_python_ast(self, changes: List[CodeChange], 
                           new_content: str, old_content: str) -> None:
        try:
            new_tree = ast.parse(new_content)
            self._annotate_with_python_nodes(changes, new_tree, new_content)
        except SyntaxError:
            pass
    
    def _annotate_with_python_nodes(self, changes: List[CodeChange], 
                                     tree: ast.AST, content: str) -> None:
        lines = content.splitlines()
        
        class FunctionVisitor(ast.NodeVisitor):
            def __init__(self):
                self.current_class = None
                self.current_function = None
                self.nodes_by_line = {}
            
            def visit_ClassDef(self, node):
                prev_class = self.current_class
                self.current_class = node.name
                self._record_node(node)
                self.generic_visit(node)
                self.current_class = prev_class
            
            def visit_FunctionDef(self, node):
                prev_func = self.current_function
                self.current_function = node.name
                self._record_node(node)
                self.generic_visit(node)
                self.current_function = prev_func
            
            def visit_AsyncFunctionDef(self, node):
                prev_func = self.current_function
                self.current_function = node.name
                self._record_node(node)
                self.generic_visit(node)
                self.current_function = prev_func
            
            def _record_node(self, node):
                if hasattr(node, 'lineno'):
                    for line in range(node.lineno, getattr(node, 'end_lineno', node.lineno) + 1):
                        self.nodes_by_line[line] = {
                            'type': type(node).__name__,
                            'function': self.current_function,
                            'class': self.current_class,
                        }
        
        visitor = FunctionVisitor()
        visitor.visit(tree)
        
        for change in changes:
            line = change.line_number
            if line in visitor.nodes_by_line:
                info = visitor.nodes_by_line[line]
                change.ast_node_type = info['type']
                change.function_name = info['function']
                change.class_name = info['class']
    
    def _enrich_javascript_ast(self, changes: List[CodeChange], 
                                new_content: str, old_content: str) -> None:
        function_pattern = r'(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?function|\s*(\w+)\s*\([^)]*\)\s*\{)'
        class_pattern = r'class\s+(\w+)'
        
        lines = new_content.splitlines()
        
        current_class = None
        function_stack = []
        
        line_info = {}
        
        for i, line in enumerate(lines, 1):
            class_match = re.search(class_pattern, line)
            if class_match:
                current_class = class_match.group(1)
            
            func_match = re.search(function_pattern, line)
            if func_match:
                func_name = func_match.group(1) or func_match.group(2) or func_match.group(3)
                if func_name:
                    function_stack.append(func_name)
            
            open_braces = line.count('{')
            close_braces = line.count('}')
            
            current_func = function_stack[-1] if function_stack else None
            
            line_info[i] = {
                'class': current_class,
                'function': current_func,
            }
            
            if close_braces > 0 and function_stack:
                for _ in range(close_braces - open_braces if close_braces > open_braces else 0):
                    if function_stack:
                        function_stack.pop()
        
        for change in changes:
            line = change.line_number
            if line in line_info:
                info = line_info[line]
                change.function_name = info['function']
                change.class_name = info['class']
                change.ast_node_type = 'statement'
    
    def _enrich_java_ast(self, changes: List[CodeChange], 
                         new_content: str, old_content: str) -> None:
        try:
            import javalang
            
            try:
                tree = javalang.parse.parse(new_content)
                
                line_info = {}
                
                for path, node in tree:
                    if hasattr(node, 'position') and node.position:
                        line = node.position.line
                        node_type = type(node).__name__
                        
                        class_name = None
                        method_name = None
                        
                        for ancestor in path:
                            if isinstance(ancestor, javalang.tree.ClassDeclaration):
                                class_name = ancestor.name
                            elif isinstance(ancestor, javalang.tree.MethodDeclaration):
                                method_name = ancestor.name
                        
                        line_info[line] = {
                            'type': node_type,
                            'class': class_name,
                            'function': method_name,
                        }
                
                for change in changes:
                    line = change.line_number
                    if line in line_info:
                        info = line_info[line]
                        change.ast_node_type = info['type']
                        change.function_name = info['function']
                        change.class_name = info['class']
            except Exception:
                pass
                
        except ImportError:
            pass
    
    def find_changes_for_error(self, error_file: str, error_line: int, 
                                analysis: ChangeAnalysis) -> List[CodeChange]:
        relevant_changes = []
        
        for change in analysis.changes:
            if change.file_path.endswith(error_file) or error_file.endswith(change.file_path):
                if (change.change_type == ChangeType.MODIFIED or
                    change.change_type == ChangeType.ADDED):
                    if change.line_number - 5 <= error_line <= change.end_line + 5:
                        relevant_changes.append(change)
        
        relevant_changes.sort(key=lambda c: abs(c.line_number - error_line))
        
        return relevant_changes
    
    def get_changed_functions(self, analysis: ChangeAnalysis) -> Dict[str, List[str]]:
        changed_functions: Dict[str, List[str]] = {}
        
        for change in analysis.changes:
            if change.function_name:
                file_key = change.file_path
                if file_key not in changed_functions:
                    changed_functions[file_key] = []
                if change.function_name not in changed_functions[file_key]:
                    changed_functions[file_key].append(change.function_name)
        
        return changed_functions
    
    def get_changed_classes(self, analysis: ChangeAnalysis) -> Dict[str, List[str]]:
        changed_classes: Dict[str, List[str]] = {}
        
        for change in analysis.changes:
            if change.class_name:
                file_key = change.file_path
                if file_key not in changed_classes:
                    changed_classes[file_key] = []
                if change.class_name not in changed_classes[file_key]:
                    changed_classes[file_key].append(change.class_name)
        
        return changed_classes
