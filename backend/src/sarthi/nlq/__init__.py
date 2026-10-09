"""Natural-language layer: chat, strategy sentences and voice commands.

The model only maps a sentence to a small fixed schema (and only when keyword rules cannot). Every
answer is computed by plain code from stored results, so there is no text-to-SQL and no tool loop.
"""
