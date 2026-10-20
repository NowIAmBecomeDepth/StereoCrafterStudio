"""StereoCrafter Studio v2 -- the Qt Quick (PySide6 + QML) front end.

Built beside the CustomTkinter Studio, which it never modifies. v2 imports
Studio's toolkit-neutral code (``depthcrafter/*``, parts of ``core``) and keeps
its own settings under ``config/``. The one file the two share on purpose is
``last_jobs.json``: a job finished here is offered to the next stage in either
Studio, which is what lets the stages be ported one at a time.
"""

__version__ = "2.0.0-dev"
