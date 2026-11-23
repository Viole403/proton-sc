"""vendor/captcha_solver — ported captcha solvers (cf-solver arkose + more).

All solvers vendored into this repo so no external runtime dependency on
cf-solver or captcha-solver repos.

Subpackages:
  arkose — Arkose FunCaptcha solver (vendor/captcha_solver/arkose/)

Usage:
  from vendor.captcha_solver.arkose.solve import solve_arkose
  result = solve_arkose(public_key="...", page_url="https://github.com/signup",
                        proxy="user:pass@host:port")
  # result: {"solved": True, "token": "...", "method": "onnx-predict", ...}
"""
from __future__ import annotations

from .arkose.solve import solve_arkose  # noqa: F401

__all__ = ["solve_arkose"]
