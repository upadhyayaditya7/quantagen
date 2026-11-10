"""Allow `python -m quantagen` as well as the console script."""

from .cli import main

raise SystemExit(main())
