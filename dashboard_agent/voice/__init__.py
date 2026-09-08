"""Voice mode: minting a Live API token, and tracing a spoken conversation.

`session` mints the short-lived Gemini token the browser uses (the key never
leaves the server); `trace` builds the LangSmith trace tree for a conversation
that happened in the browser rather than in the graph.

Re-exported here so `webapp.py` imports one place rather than two modules.
"""

from dashboard_agent.voice.session import mint_token, voice_configured

__all__ = ["mint_token", "voice_configured"]
