---
name: rag_lookup
description: 只根据 rag 和 rag2 里的文档回答问题，不把资料库里的内容编出来。
when-to-use: 用户问知识库、文档、RAG、小区资料或 valuation 以外的本地资料时。
---

资料只来自这些目录：

- `rag/docs/`
- `rag2/docs/`
- `rag/SIMPLE_RAG.md`
- `rag2/RAG2.md`

先 `list_files` 看有哪些文档，再用 `grep_search` 找和问题相关的句子，然后 `read_file` 读中的那段。回答时引用文件名。

文档里没有的内容，要写明「本地文档里没有找到」。不要改写向量库，也不要运行建库脚本，除非用户明确要求重建索引。
