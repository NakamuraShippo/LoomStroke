"""SAM 3.1 runtime integration (worker, setup engine, compatibility layer).

Import-pure by contract: importing this package (or any module in it that the
UI process touches) must not pull in PySide6, torch, numpy, or the sam3
runtime. Heavy imports happen lazily inside functions, or only inside the
worker process which runs in the dedicated SAM 3.1 environment.

The legacy ``loomstroke.ai_selection`` package remains the production backend during
the migration; this package must not depend on its internals (shared ideas
are re-implemented against the same contracts so SAM3 can be deleted in
Phase D without touching SAM31).
"""
