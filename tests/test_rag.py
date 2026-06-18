import os
import tempfile
import pytest

from src.rag import DocumentRetriever, RetrievalResult, VectorStore, Document


class TestVectorStore:
    def test_empty_store(self):
        store = VectorStore(openai_api_key="", chunk_size=100, chunk_overlap=10)
        results = store.similarity_search("test query", k=3)
        
        assert isinstance(results, list)
        assert len(results) == 0
    
    def test_add_documents(self):
        store = VectorStore(openai_api_key="", chunk_size=100, chunk_overlap=10)
        
        docs = [
            Document(content="Python is a programming language", source="doc1.txt"),
            Document(content="Java is another language", source="doc2.txt"),
        ]
        
        store.add_documents(docs)
        assert len(store._documents) == 2
    
    def test_simple_search(self):
        store = VectorStore(openai_api_key="", chunk_size=100, chunk_overlap=10)
        
        docs = [
            Document(content="Python function definition with def keyword", source="py1.txt"),
            Document(content="JavaScript function with function keyword", source="js1.txt"),
        ]
        
        store.add_documents(docs)
        results = store.similarity_search("Python function", k=2)
        
        assert len(results) > 0
        assert isinstance(results[0], Document)


class TestDocumentRetriever:
    def test_index_project_python(self, tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        
        py_file = src_dir / "example.py"
        py_file.write_text('''
def add(a, b):
    """Add two numbers together.
    
    Args:
        a: First number
        b: Second number
    
    Returns:
        Sum of a and b
    """
    return a + b

class Calculator:
    """A simple calculator class."""
    
    def multiply(self, x, y):
        """Multiply two numbers."""
        return x * y
''')
        
        retriever = DocumentRetriever(repo_path=str(tmp_path), openai_api_key="")
        retriever.index_project()
        
        assert retriever._indexed == True
    
    def test_retrieve_empty(self, tmp_path):
        retriever = DocumentRetriever(repo_path=str(tmp_path), openai_api_key="")
        
        result = retriever.retrieve("test query")
        
        assert isinstance(result, RetrievalResult)
    
    def test_get_file_content(self, tmp_path):
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello World")
        
        retriever = DocumentRetriever(repo_path=str(tmp_path), openai_api_key="")
        content = retriever.get_file_content("test.txt")
        
        assert content == "Hello World"
    
    def test_get_file_content_not_found(self, tmp_path):
        retriever = DocumentRetriever(repo_path=str(tmp_path), openai_api_key="")
        content = retriever.get_file_content("nonexistent.txt")
        
        assert content is None
    
    def test_retrieval_result_no_results(self):
        result = RetrievalResult()
        
        assert not result.has_results
        assert result.count == 0
    
    def test_retrieval_result_formatted_context(self):
        result = RetrievalResult(
            documents=[
                Document(content="Content 1", source="file1.py"),
                Document(content="Content 2", source="file2.py"),
            ],
            query="test",
        )
        
        context = result.get_formatted_context()
        
        assert "Content 1" in context
        assert "Content 2" in context
        assert "file1.py" in context
        assert "file2.py" in context


class TestDocument:
    def test_document_creation(self):
        doc = Document(
            content="test content",
            source="test.py",
            metadata={"type": "function"},
        )
        
        assert doc.content == "test content"
        assert doc.source == "test.py"
        assert doc.metadata["type"] == "function"
    
    def test_document_default_metadata(self):
        doc = Document(content="test", source="test.txt")
        
        assert doc.metadata == {}
