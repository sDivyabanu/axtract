"""DealLens: retrieval-augmented, auditable Q&A on top of the ParseAnything parser.

All components are local (SQLite, numpy, ONNX embeddings/reranker, Ollama LLM). Nothing here
makes a network call except to the local Ollama server.
"""
