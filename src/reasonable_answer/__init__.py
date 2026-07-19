"""reasonable-answer — an alternating write/critique game with a blind referee.

See docs/DESIGN.md. The public surface is deliberately small:

    from reasonable_answer import Config, run
"""

from .config import Config, ConfigError
from .graph import run

__all__ = ["Config", "ConfigError", "run"]
