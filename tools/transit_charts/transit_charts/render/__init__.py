"""Rendering layer - matplotlib lives here and nowhere else.

Importing this package pulls matplotlib in, so the extraction modules deliberately do not.
See the top-level README for why that separation is structural (Termux has no matplotlib
wheels) rather than a matter of taste.
"""

__all__ = ["crosscity", "headway", "html", "punctuality", "speed", "style", "trajectory"]
