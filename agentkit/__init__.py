"""agentkit: the small helper library behind every demo and lab in this course.

    from agentkit import llm, tools
    client = llm.get_llm()
    reply = client.chat(messages, tools=toolbox.schemas())

Everything here is plain Python standard library, except the optional
Amazon Bedrock adapter, which needs boto3.
"""
from . import config  # noqa: F401  (loads .env on import)

__all__ = ["config", "llm", "tools", "policy", "agent", "ui", "mock_llm"]
