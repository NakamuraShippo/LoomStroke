"""Direct WGPU vector rendering backend (retained-mode pipeline).

Package layout follows
``docs/dev/plan/future/vector/LOOMSTROKE_WGPU_RETAINED_VECTOR_RENDERING_DESIGN_SPEC.md``
(§22).  CPU geometry compilation modules in this package must stay importable
without ``wgpu`` and without Qt so they can run on worker threads.
"""
