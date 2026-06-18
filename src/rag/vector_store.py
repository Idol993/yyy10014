import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from pathlib import Path


@dataclass
class Document:
    content: str
    source: str
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class RetrievalResult:
    documents: List[Document] = field(default_factory=list)
    query: str = ""
    
    @property
    def has_results(self) -> bool:
        return len(self.documents) > 0
    
    @property
    def count(self) -> int:
        return len(self.documents)
    
    def get_formatted_context(self) -> str:
        parts = []
        for i, doc in enumerate(self.documents, 1):
            parts.append(f"--- Document {i}: {doc.source} ---")
            parts.append(doc.content)
            parts.append("")
        return "\n".join(parts)


class VectorStore:
    def __init__(self, openai_api_key: str, chunk_size: int = 1000, chunk_overlap: int = 200):
        self.openai_api_key = openai_api_key
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._documents: List[Document] = []
        self._embeddings = None
        self._vector_store = None
    
    def add_documents(self, documents: List[Document]) -> None:
        self._documents.extend(documents)
        self._build_vector_store()
    
    def _build_vector_store(self) -> None:
        try:
            from langchain_openai import OpenAIEmbeddings
            from langchain_community.vectorstores import FAISS
            from langchain_text_splitters import RecursiveCharacterTextSplitter
            
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
                length_function=len,
            )
            
            texts = []
            metadatas = []
            
            for doc in self._documents:
                chunks = text_splitter.split_text(doc.content)
                for chunk in chunks:
                    texts.append(chunk)
                    metadata = {"source": doc.source}
                    metadata.update(doc.metadata)
                    metadatas.append(metadata)
            
            embeddings = OpenAIEmbeddings(openai_api_key=self.openai_api_key)
            
            self._vector_store = FAISS.from_texts(
                texts=texts,
                embedding=embeddings,
                metadatas=metadatas,
            )
            
        except ImportError as e:
            print(f"Warning: Could not build vector store: {e}")
            self._vector_store = None
    
    def similarity_search(self, query: str, k: int = 5) -> List[Document]:
        if self._vector_store is None:
            return self._simple_search(query, k)
        
        try:
            results = self._vector_store.similarity_search(query, k=k)
            documents = []
            for result in results:
                documents.append(Document(
                    content=result.page_content,
                    source=result.metadata.get("source", "unknown"),
                    metadata=result.metadata,
                ))
            return documents
        except Exception as e:
            print(f"Warning: Vector search failed, falling back to simple search: {e}")
            return self._simple_search(query, k)
    
    def _simple_search(self, query: str, k: int = 5) -> List[Document]:
        query_terms = set(query.lower().split())
        scored_docs = []
        
        for doc in self._documents:
            content_lower = doc.content.lower()
            score = sum(1 for term in query_terms if term in content_lower)
            if score > 0:
                scored_docs.append((score, doc))
        
        scored_docs.sort(key=lambda x: x[0], reverse=True)
        return [doc for score, doc in scored_docs[:k]]
