"""Reproducible performance benchmarks for the Folith Qt desktop client.

The harness drives the *real* :class:`~folith.qt.window.MainWindow` (offscreen
platform) against the *real* core worker so the numbers describe the shipping
application rather than a mock of it.

Entry point: ``python -m benchmarks.performance.run_benchmark``.
"""
