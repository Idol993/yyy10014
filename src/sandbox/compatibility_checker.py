import os
import re
import ast
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple, Set
from enum import Enum


class CompatibilitySeverity(Enum):
    BREAKING = "breaking"
    WARNING = "warning"
    INFO = "info"


@dataclass
class CompatibilityIssue:
    file_path: str
    element_name: str
    element_type: str
    severity: CompatibilitySeverity
    message: str
    details: str = ""
    line_number: Optional[int] = None
    
    @property
    def is_breaking(self) -> bool:
        return self.severity == CompatibilitySeverity.BREAKING


@dataclass
class CompatibilityReport:
    issues: List[CompatibilityIssue] = field(default_factory=list)
    changed_files: List[str] = field(default_factory=list)
    removed_apis: List[str] = field(default_factory=list)
    modified_signatures: List[str] = field(default_factory=list)
    
    @property
    def has_breaking_changes(self) -> bool:
        return any(i.is_breaking for i in self.issues)
    
    @property
    def total_issues(self) -> int:
        return len(self.issues)


class CompatibilityChecker:
    def __init__(self, repo_path: str = "."):
        self.repo_path = repo_path
    
    def check_compatibility(self, old_file_content: str, new_file_content: str,
                            file_path: str) -> CompatibilityReport:
        report = CompatibilityReport(changed_files=[file_path])
        
        ext = os.path.splitext(file_path)[1]
        
        if ext == '.py':
            issues = self._check_python_compatibility(old_file_content, new_file_content, file_path)
        elif ext in ('.js', '.ts', '.jsx', '.tsx'):
            issues = self._check_javascript_compatibility(old_file_content, new_file_content, file_path)
        elif ext == '.java':
            issues = self._check_java_compatibility(old_file_content, new_file_content, file_path)
        else:
            issues = self._generic_compatibility_check(old_file_content, new_file_content, file_path)
        
        report.issues = issues
        
        for issue in issues:
            if issue.severity == CompatibilitySeverity.BREAKING:
                if issue.element_type == "function_removal":
                    report.removed_apis.append(issue.element_name)
                elif issue.element_type == "signature_change":
                    report.modified_signatures.append(issue.element_name)
        
        return report
    
    def _check_python_compatibility(self, old_content: str, new_content: str,
                                     file_path: str) -> List[CompatibilityIssue]:
        issues = []
        
        try:
            old_tree = ast.parse(old_content)
            new_tree = ast.parse(new_content)
            
            old_funcs = self._extract_python_functions(old_tree)
            new_funcs = self._extract_python_functions(new_tree)
            
            old_classes = self._extract_python_classes(old_tree)
            new_classes = self._extract_python_classes(new_tree)
            
            for func_name, func_info in old_funcs.items():
                if func_info.get('is_public', False):
                    if func_name not in new_funcs:
                        issues.append(CompatibilityIssue(
                            file_path=file_path,
                            element_name=func_name,
                            element_type="function_removal",
                            severity=CompatibilitySeverity.BREAKING,
                            message=f"Public function '{func_name}' was removed",
                            details="Removing a public function breaks backward compatibility",
                            line_number=func_info.get('lineno'),
                        ))
                    else:
                        old_sig = func_info.get('args', [])
                        new_sig = new_funcs[func_name].get('args', [])
                        
                        old_required = [a for a in old_sig if not a.get('has_default', False)]
                        new_required = [a for a in new_sig if not a.get('has_default', False)]
                        
                        added_required = len(new_required) > len(old_required)
                        
                        if added_required:
                            issues.append(CompatibilityIssue(
                                file_path=file_path,
                                element_name=func_name,
                                element_type="signature_change",
                                severity=CompatibilitySeverity.BREAKING,
                                message=f"Function '{func_name}' added required parameters",
                                details=f"Old required: {[a['name'] for a in old_required]}, New required: {[a['name'] for a in new_required]}",
                                line_number=new_funcs[func_name].get('lineno'),
                            ))
            
            for class_name, class_info in old_classes.items():
                if class_info.get('is_public', False):
                    if class_name not in new_classes:
                        issues.append(CompatibilityIssue(
                            file_path=file_path,
                            element_name=class_name,
                            element_type="class_removal",
                            severity=CompatibilitySeverity.BREAKING,
                            message=f"Public class '{class_name}' was removed",
                            line_number=class_info.get('lineno'),
                        ))
                    else:
                        old_methods = class_info.get('methods', {})
                        new_methods = new_classes.get(class_name, {}).get('methods', {})
                        
                        for method_name, method_info in old_methods.items():
                            if method_info.get('is_public', False):
                                if method_name not in new_methods:
                                    issues.append(CompatibilityIssue(
                                        file_path=file_path,
                                        element_name=f"{class_name}.{method_name}",
                                        element_type="method_removal",
                                        severity=CompatibilitySeverity.BREAKING,
                                        message=f"Public method '{class_name}.{method_name}' was removed",
                                    ))
                        
                        for var_name in class_info.get('attributes', set()):
                            if self._is_public_name(var_name):
                                new_class_info = new_classes.get(class_name, {})
                                new_attrs = new_class_info.get('attributes', set())
                                if var_name not in new_attrs:
                                    issues.append(CompatibilityIssue(
                                        file_path=file_path,
                                        element_name=f"{class_name}.{var_name}",
                                        element_type="attribute_removal",
                                        severity=CompatibilitySeverity.BREAKING,
                                        message=f"Public attribute '{class_name}.{var_name}' was removed",
                                    ))
            
        except SyntaxError:
            pass
        
        return issues
    
    def _extract_python_functions(self, tree: ast.AST) -> Dict[str, Dict]:
        functions = {}
        
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = []
                for arg in node.args.args:
                    has_default = False
                    if hasattr(node.args, 'defaults'):
                        defaults_count = len(node.args.defaults)
                        positional_count = len(node.args.args)
                        arg_index = node.args.args.index(arg)
                        has_default = arg_index >= positional_count - defaults_count
                    
                    args.append({
                        'name': arg.arg,
                        'has_default': has_default,
                    })
                
                functions[node.name] = {
                    'lineno': node.lineno,
                    'args': args,
                    'is_public': self._is_public_name(node.name),
                    'is_async': isinstance(node, ast.AsyncFunctionDef),
                }
        
        return functions
    
    def _extract_python_classes(self, tree: ast.AST) -> Dict[str, Dict]:
        classes = {}
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = {}
                attributes = set()
                
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_args = []
                        for arg in item.args.args:
                            method_args.append({
                                'name': arg.arg,
                                'has_default': False,
                            })
                        
                        methods[item.name] = {
                            'lineno': item.lineno,
                            'args': method_args,
                            'is_public': self._is_public_name(item.name),
                        }
                    elif isinstance(item, ast.Assign):
                        for target in item.targets:
                            if isinstance(target, ast.Name):
                                attributes.add(target.id)
                
                classes[node.name] = {
                    'lineno': node.lineno,
                    'methods': methods,
                    'attributes': attributes,
                    'is_public': self._is_public_name(node.name),
                    'bases': [base.id for base in node.bases if isinstance(base, ast.Name)],
                }
        
        return classes
    
    def _is_public_name(self, name: str) -> bool:
        return not name.startswith('_')
    
    def _check_javascript_compatibility(self, old_content: str, new_content: str,
                                         file_path: str) -> List[CompatibilityIssue]:
        issues = []
        
        old_exports = self._extract_js_exports(old_content)
        new_exports = self._extract_js_exports(new_content)
        
        for export_name in old_exports:
            if export_name not in new_exports:
                issues.append(CompatibilityIssue(
                    file_path=file_path,
                    element_name=export_name,
                    element_type="export_removal",
                    severity=CompatibilitySeverity.BREAKING,
                    message=f"Export '{export_name}' was removed",
                    details="Removing an export breaks backward compatibility for consumers",
                ))
        
        return issues
    
    def _extract_js_exports(self, content: str) -> Set[str]:
        exports = set()
        
        patterns = [
            r'export\s+(?:default\s+)?(?:function|class|const|let|var)\s+(\w+)',
            r'export\s*\{([^}]+)\}',
            r'module\.exports\.(\w+)\s*=',
            r'module\.exports\s*=\s*(?:\{)?(\w*)',
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, content):
                if match.group(1):
                    if ',' in match.group(1):
                        for name in match.group(1).split(','):
                            name = name.strip().split(' as ')[0].strip()
                            if name:
                                exports.add(name)
                    else:
                        name = match.group(1).strip()
                        if name:
                            exports.add(name)
        
        return exports
    
    def _check_java_compatibility(self, old_content: str, new_content: str,
                                    file_path: str) -> List[CompatibilityIssue]:
        issues = []
        
        old_methods = self._extract_java_public_methods(old_content)
        new_methods = self._extract_java_public_methods(new_content)
        
        for method_sig in old_methods:
            if method_sig not in new_methods:
                issues.append(CompatibilityIssue(
                    file_path=file_path,
                    element_name=method_sig,
                    element_type="method_removal",
                    severity=CompatibilitySeverity.BREAKING,
                    message=f"Public method '{method_sig}' was removed",
                ))
        
        return issues
    
    def _extract_java_public_methods(self, content: str) -> Set[str]:
        methods = set()
        
        pattern = r'public\s+(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?[\w<>,\[\]\s]+?\s+(\w+)\s*\([^)]*\)'
        
        for match in re.finditer(pattern, content):
            method_name = match.group(1)
            methods.add(method_name)
        
        return methods
    
    def _generic_compatibility_check(self, old_content: str, new_content: str,
                                      file_path: str) -> List[CompatibilityIssue]:
        issues = []
        
        old_lines = set(old_content.split('\n'))
        new_lines = set(new_content.split('\n'))
        
        removed_lines = old_lines - new_lines
        
        api_patterns = [
            (r'^#define\s+(\w+)', 'macro'),
            (r'^const\s+\w+\s+(\w+)', 'const'),
            (r'^func\s+(\w+)', 'function'),
        ]
        
        for line in removed_lines:
            line = line.strip()
            for pattern, element_type in api_patterns:
                match = re.match(pattern, line)
                if match:
                    name = match.group(1)
                    if not name.startswith('_'):
                        issues.append(CompatibilityIssue(
                            file_path=file_path,
                            element_name=name,
                            element_type=element_type,
                            severity=CompatibilitySeverity.WARNING,
                            message=f"Potential API change: {element_type} '{name}' may have been removed or modified",
                        ))
        
        return issues
    
    def check_public_api_stability(self, files: List[str], 
                                    old_revision: str, new_revision: str) -> CompatibilityReport:
        report = CompatibilityReport(changed_files=files)
        
        try:
            import git
            repo = git.Repo(self.repo_path)
            
            for file_path in files:
                try:
                    old_content = repo.git.show(f"{old_revision}:{file_path}")
                    new_content = repo.git.show(f"{new_revision}:{file_path}")
                    
                    file_report = self.check_compatibility(old_content, new_content, file_path)
                    report.issues.extend(file_report.issues)
                    
                except Exception:
                    pass
                    
        except ImportError:
            pass
        
        return report
