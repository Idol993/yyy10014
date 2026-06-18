import os
import re
import ast
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Set
from pathlib import Path

from .vector_store import VectorStore, Document, RetrievalResult


class DocumentRetriever:
    def __init__(self, repo_path: str = ".", openai_api_key: str = "",
                 chunk_size: int = 1000, chunk_overlap: int = 200, top_k: int = 5):
        self.repo_path = repo_path
        self.top_k = top_k
        self.vector_store = VectorStore(
            openai_api_key=openai_api_key,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        self._indexed = False
    
    def index_project(self) -> None:
        if self._indexed:
            return
        
        documents: List[Document] = []
        
        documents.extend(self._extract_code_documents())
        documents.extend(self._extract_readme_documents())
        documents.extend(self._extract_config_documents())
        
        self.vector_store.add_documents(documents)
        self._indexed = True
    
    def _extract_code_documents(self) -> List[Document]:
        documents = []
        
        code_extensions = {'.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.go', '.rs', '.cpp', '.c', '.h'}
        
        for root, dirs, files in os.walk(self.repo_path):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in 
                       ('node_modules', 'venv', '__pycache__', 'build', 'dist', 'target')]
            
            for file in files:
                file_path = os.path.join(root, file)
                ext = os.path.splitext(file)[1]
                
                if ext in code_extensions:
                    docs = self._extract_docstrings_from_file(file_path, ext)
                    documents.extend(docs)
        
        return documents
    
    def _extract_docstrings_from_file(self, file_path: str, ext: str) -> List[Document]:
        documents = []
        
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            
            rel_path = os.path.relpath(file_path, self.repo_path)
            
            if ext == '.py':
                documents.extend(self._extract_python_docstrings(content, rel_path))
            elif ext in ('.js', '.ts', '.jsx', '.tsx'):
                documents.extend(self._extract_javascript_docs(content, rel_path))
            elif ext == '.java':
                documents.extend(self._extract_java_docs(content, rel_path))
            
            if len(content) < 5000:
                documents.append(Document(
                    content=content,
                    source=rel_path,
                    metadata={"type": "full_file", "language": ext[1:]},
                ))
            
        except Exception as e:
            print(f"Warning: Could not process {file_path}: {e}")
        
        return documents
    
    def _extract_python_docstrings(self, content: str, source: str) -> List[Document]:
        documents = []
        
        try:
            tree = ast.parse(content)
            
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
                    docstring = ast.get_docstring(node)
                    if docstring:
                        name = node.name if hasattr(node, 'name') else 'module'
                        node_type = type(node).__name__
                        
                        full_text = f"{node_type} {name}\n\nDocstring:\n{docstring}"
                        
                        if hasattr(node, 'lineno'):
                            full_text += f"\n\nLocation: line {node.lineno}"
                        
                        documents.append(Document(
                            content=full_text,
                            source=source,
                            metadata={
                                "type": "docstring",
                                "name": name,
                                "node_type": node_type,
                                "language": "python",
                            },
                        ))
        except SyntaxError:
            pass
        
        return documents
    
    def _extract_javascript_docs(self, content: str, source: str) -> List[Document]:
        documents = []
        
        jsdoc_pattern = r'/\*\*(.*?)\*/\s*(?:export\s+)?(?:(?:async\s+)?function|const|let|var|class)\s+(\w+)'
        
        for match in re.finditer(jsdoc_pattern, content, re.DOTALL):
            doc_content = match.group(1).strip()
            name = match.group(2)
            
            lines = []
            for line in doc_content.split('\n'):
                line = line.strip()
                if line.startswith('* '):
                    line = line[2:]
                elif line == '*':
                    line = ''
                lines.append(line)
            
            doc_text = f"Function/Class: {name}\n\nDocumentation:\n{'\n'.join(lines)}"
            
            documents.append(Document(
                content=doc_text,
                source=source,
                metadata={
                    "type": "jsdoc",
                    "name": name,
                    "language": "javascript",
                },
            ))
        
        return documents
    
    def _extract_java_docs(self, content: str, source: str) -> List[Document]:
        documents = []
        
        javadoc_pattern = r'/\*\*(.*?)\*/\s*(?:public|private|protected)?\s*(?:static\s+)?(?:class|interface|enum|@?\w+)\s+(\w+)'
        
        for match in re.finditer(javadoc_pattern, content, re.DOTALL):
            doc_content = match.group(1).strip()
            name = match.group(2)
            
            lines = []
            for line in doc_content.split('\n'):
                line = line.strip()
                if line.startswith('* '):
                    line = line[2:]
                elif line == '*':
                    line = ''
                lines.append(line)
            
            doc_text = f"Class/Interface: {name}\n\nJavadoc:\n{'\n'.join(lines)}"
            
            documents.append(Document(
                content=doc_text,
                source=source,
                metadata={
                    "type": "javadoc",
                    "name": name,
                    "language": "java",
                },
            ))
        
        return documents
    
    def _extract_readme_documents(self) -> List[Document]:
        documents = []
        
        readme_names = ['README.md', 'README.rst', 'README.txt', 'README']
        
        for name in readme_names:
            readme_path = os.path.join(self.repo_path, name)
            if os.path.exists(readme_path):
                with open(readme_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                
                documents.append(Document(
                    content=content,
                    source=name,
                    metadata={"type": "readme"},
                ))
                break
        
        docs_dir = os.path.join(self.repo_path, 'docs')
        if os.path.isdir(docs_dir):
            for root, dirs, files in os.walk(docs_dir):
                for file in files:
                    if file.endswith(('.md', '.rst', '.txt')):
                        file_path = os.path.join(root, file)
                        rel_path = os.path.relpath(file_path, self.repo_path)
                        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                        documents.append(Document(
                            content=content,
                            source=rel_path,
                            metadata={"type": "documentation"},
                        ))
        
        return documents
    
    def _extract_config_documents(self) -> List[Document]:
        documents = []
        
        config_files = [
            'package.json',
            'requirements.txt',
            'pyproject.toml',
            'setup.py',
            'setup.cfg',
            'pom.xml',
            'build.gradle',
            'go.mod',
            'Cargo.toml',
            'tsconfig.json',
            '.eslintrc.json',
            'Makefile',
            'CMakeLists.txt',
        ]
        
        for config_file in config_files:
            config_path = os.path.join(self.repo_path, config_file)
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                
                documents.append(Document(
                    content=content,
                    source=config_file,
                    metadata={"type": "config", "config_type": config_file},
                ))
        
        return documents
    
    def retrieve(self, query: str, error_context: Optional[str] = None,
                 file_path: Optional[str] = None) -> RetrievalResult:
        self.index_project()
        
        enhanced_query = query
        if error_context:
            enhanced_query += f"\n\nError context: {error_context}"
        if file_path:
            enhanced_query += f"\n\nRelated to file: {file_path}"
        
        docs = self.vector_store.similarity_search(enhanced_query, k=self.top_k)
        
        return RetrievalResult(
            documents=docs,
            query=query,
        )
    
    def retrieve_for_error(self, error_type: str, error_message: str,
                           file_path: Optional[str] = None,
                           function_name: Optional[str] = None) -> RetrievalResult:
        self.index_project()
        
        query_parts = [error_type, error_message]
        
        if function_name:
            query_parts.append(f"function {function_name}")
        
        query = " ".join(query_parts)
        
        docs = self.vector_store.similarity_search(query, k=self.top_k)
        
        return RetrievalResult(
            documents=docs,
            query=query,
        )
    
    def get_file_content(self, file_path: str) -> Optional[str]:
        full_path = os.path.join(self.repo_path, file_path)
        if os.path.exists(full_path):
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        return None
    
    def get_function_source(self, file_path: str, function_name: str) -> Optional[str]:
        content = self.get_file_content(file_path)
        if not content:
            return None
        
        ext = os.path.splitext(file_path)[1]
        
        if ext == '.py':
            return self._get_python_function_source(content, function_name)
        
        return None
    
    def _get_python_function_source(self, content: str, function_name: str) -> Optional[str]:
        try:
            tree = ast.parse(content)
            
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name == function_name:
                        import astunparse
                        return astunparse.unparse(node)
        except Exception:
            pass
        
        lines = content.splitlines()
        pattern = rf'^\s*(?:async\s+)?def\s+{re.escape(function_name)}\s*\('
        
        start_line = None
        for i, line in enumerate(lines):
            if re.match(pattern, line):
                start_line = i
                break
        
        if start_line is not None:
            end_line = self._find_function_end(lines, start_line)
            return "\n".join(lines[start_line:end_line + 1])
        
        return None
    
    def _find_function_end(self, lines: List[str], start_line: int) -> int:
        indent = len(lines[start_line]) - len(lines[start_line].lstrip())
        
        for i in range(start_line + 1, len(lines)):
            if lines[i].strip() and not lines[i].startswith(' ' * (indent + 1)):
                if not lines[i].strip().startswith('#'):
                    return i - 1
        
        return len(lines) - 1
