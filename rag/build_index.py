import hashlib
import os
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

PROJECT_DIR = Path(__file__).resolve().parent
SUPPORTED_TEXT_EXTENSIONS = {".md", ".txt"}
EMBEDDING_BATCH_SIZE = 32


def load_documents(directory: Path = PROJECT_DIR / "documents") -> list[Document]:
    """Load top-level Markdown, text, and PDF documents from the corpus."""
    if not directory.is_dir():
        raise FileNotFoundError(f"RAG documents directory does not exist: {directory}")

    documents: list[Document] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue

        if path.suffix.lower() == ".pdf":
            reader = PdfReader(str(path))
            for page_number, page in enumerate(reader.pages, start=1):
                content = (page.extract_text() or "").strip()
                if content:
                    documents.append(
                        Document(
                            page_content=content,
                            metadata={"source": path.name, "page": page_number},
                        )
                    )
        elif path.suffix.lower() in SUPPORTED_TEXT_EXTENSIONS:
            content = path.read_text(encoding="utf-8").strip()
            if content:
                documents.append(
                    Document(page_content=content, metadata={"source": path.name})
                )

    if not documents:
        raise ValueError(f"No readable text or PDF documents found in {directory}")

    return documents



def build_rag() -> Chroma:
    documents_dir = Path(
        os.environ.get("RAG_DOCUMENTS_DIR", str(PROJECT_DIR / "documents"))
    ).expanduser()
    documents = load_documents(documents_dir)
    
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    chunks = splitter.split_documents(documents)
    
    embeddings = OllamaEmbeddings(
        model=os.environ.get("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
    )
    index_dir = Path(
        os.environ.get("RAG_VECTOR_STORE_DIR", str(PROJECT_DIR / "chroma_db"))
    ).expanduser()
    index_dir.mkdir(parents=True, exist_ok=True)
    vector_store = Chroma(
        embedding_function=embeddings,
        persist_directory=str(index_dir),
    )

    chunk_numbers: dict[tuple[str, int | None], int] = {}
    chunk_ids: list[str] = []
    for chunk in chunks:
        source = str(chunk.metadata.get("source", "unknown"))
        page = chunk.metadata.get("page")
        key = (source, page)
        chunk_number = chunk_numbers.get(key, 0)
        chunk_numbers[key] = chunk_number + 1
        chunk_key = f"{source}\0{page}\0{chunk_number}".encode("utf-8")
        chunk_ids.append(hashlib.sha256(chunk_key).hexdigest())

    for start in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
        batch = chunks[start : start + EMBEDDING_BATCH_SIZE]
        vector_store.add_documents(
            documents=batch,
            ids=chunk_ids[start : start + EMBEDDING_BATCH_SIZE],
        )
        print(f"Indexed {min(start + len(batch), len(chunks))}/{len(chunks)} chunks")

    return vector_store


if __name__ == "__main__":
    build_rag()