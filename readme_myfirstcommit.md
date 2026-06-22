# Ethel local Adaptation commit
## Code adaptation for local use and debugging
The original Ethel project relied on Azure OpenAI models and containerised deployment approach based on Kubernetes. To enable local testing, the code was modified so that OpenAI API keys could be used for LLM access, while containerisation is done based on Docker Compose instead of Kubernetes. 

## RAG adaptation
Original code contained several issues that had to be resolved to make the RAG chat mode functional. In particular, additional batching of document chunks was implemented to support embedding generation for long PDF documents. One remaining issue concerns access to saved embedded documents through the app. At present, this has been addressed by using a hard-coded list of documents IDs that are passed to the RAG chat mode when Ethel is launched though the app rather than via the console.

## API key
The file .env should contain a valid OpenAI API key. Substitute the file Content with your real API key when running Ethel on your machine.