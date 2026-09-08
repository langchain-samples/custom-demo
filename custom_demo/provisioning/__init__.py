"""Building a demo: the assistant, its prompts, its eval dataset, its traffic.

Nothing here runs on a chat turn. It runs once, when a presenter creates an
assistant, which is why it may reach for slow LangSmith calls that the runtime
never would.
"""
