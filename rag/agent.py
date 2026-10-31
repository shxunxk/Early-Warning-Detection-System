"""Local RAG agent for project documents.

Requires Ollama to be running with the configured chat and embedding models
available. By default, top-level .txt and .md files beside this script are
indexed; set RAG_DOCUMENTS_DIR to use a different document folder.
"""

import os
import sys
from pathlib import Path

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_chroma import Chroma
from langchain_ollama import ChatOllama, OllamaEmbeddings


PROJECT_DIR = Path(__file__).resolve().parent
SYSTEM_PROMPT = """You answer questions using the project knowledge base.
Use the search_knowledge_base tool for questions about project-specific facts.
Base factual claims on retrieved passages, cite their source filenames, and say
when the knowledge base does not contain enough information. Do not invent
policies, figures, or citations."""




def build_agent():
    """Build a LangGraph-backed agent with a local Chroma retrieval tool."""
    index_dir = Path(
        os.environ.get("RAG_VECTOR_STORE_DIR", str(PROJECT_DIR / "chroma_db"))
    ).expanduser()
    if not (index_dir / "chroma.sqlite3").is_file():
        raise FileNotFoundError(
            f"Chroma index not found at {index_dir}. Run build_index.py first."
        )

    vector_store = Chroma(
        embedding_function=OllamaEmbeddings(
            model=os.environ.get("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
        ),
        persist_directory=str(index_dir),
    )
    retriever = vector_store.as_retriever(search_kwargs={"k": 4})

    @tool
    def search_knowledge_base(query: str) -> str:
        """Find relevant passages in the project documents for a question."""
        matches = retriever.invoke(query)
        if not matches:
            return "No relevant passages were found in the knowledge base."

        return "\n\n".join(
            f"Source: {document.metadata.get('source', 'unknown')}\n"
            f"{document.page_content}"
            for document in matches
        )

    model = ChatOllama(
        model=os.environ.get("OLLAMA_CHAT_MODEL", "llama3.1"),
        temperature=0,
    )
    return create_agent(
        model=model,
        tools=[search_knowledge_base],
        system_prompt=SYSTEM_PROMPT,
    )


def ask(agent, question: str) -> str:
    """Send one question to the agent and return its final response."""
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    return result["messages"][-1].content


def main() -> None:
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        question = input("Ask a question about the project: ").strip()
    if not question:
        raise SystemExit("A non-empty question is required.")

    print(ask(build_agent(), question))


if __name__ == "__main__":
    main()