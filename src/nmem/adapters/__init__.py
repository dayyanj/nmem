"""Framework adapters for LangChain, CrewAI, and plain synchronous usage.

Usage::

    # LangChain (pip install nmem[langchain])
    from nmem.adapters.langchain import NmemLangChainMemory

    # CrewAI (pip install nmem[crewai])
    from nmem.adapters.crewai import NmemCrewAIMemory

    # Plain sync wrapper (no extra deps)
    from nmem.adapters.plain import NmemSyncWrapper
"""
