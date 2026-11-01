"""`python -m qa_audit`: the same command as `qa-audit`, for a computer whose `qa-audit` launcher points at a Python that
is no longer installed (a pip launcher is bound to the interpreter that installed it)."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
