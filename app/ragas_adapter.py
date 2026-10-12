"""RAGAS 0.2.15 adapters; judge and embedding use the same budgeted call layer."""
def make_adapters(settings):
    from ragas.llms.base import BaseRagasLLM
    from ragas.embeddings.base import BaseRagasEmbeddings
    from langchain_core.outputs import LLMResult, Generation
    from ragas.run_config import RunConfig
    from .models import CloudModels
    import litellm
    import asyncio
    model=CloudModels(settings)

    class Judge(BaseRagasLLM):
        def is_finished(self,response): return True
        def generate_text(self,prompt,n=1,temperature=0,stop=None,callbacks=None):
            return asyncio.run(self.agenerate_text(prompt,n,temperature,stop,callbacks))
        async def agenerate_text(self,prompt,n=1,temperature=0,stop=None,callbacks=None):
            generations=[]
            for _ in range(n):
                messages=[{'role':'user','content':prompt.to_string()}]
                model.check_request(messages,2000)
                response=await model.budget.run(litellm.acompletion,settings.llm_model,fallback=settings.fallback_model,
                    messages=messages,temperature=temperature,
                    stop=stop,max_tokens=2000,**model.kwargs())
                generations.append(Generation(text=response.choices[0].message.content))
                model.usage.append({'operation':'judge','model':response.model,
                    'usage':response.usage.model_dump() if response.usage else None})
            return LLMResult(generations=[generations])

    class Embeddings(BaseRagasEmbeddings):
        def embed_query(self,text): return asyncio.run(self.aembed_query(text))
        def embed_documents(self,texts): return asyncio.run(self.aembed_documents(texts))
        async def aembed_query(self,text): return (await model.embed_query([text]))[0]
        async def aembed_documents(self,texts): return await model.embed(texts)

    judge,embedding=Judge(),Embeddings()
    config=RunConfig(timeout=settings.request_timeout,max_retries=1)
    judge.set_run_config(config);embedding.set_run_config(config)
    return judge,embedding,model
