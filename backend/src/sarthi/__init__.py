import os

# statsforecast compiles its models with numba; keep the compiled code on disk instead of compiling them
# again in every process (and in every worker process of a parallel fit). statsforecast reads this when it is
# imported, so it is set here, before any module of the package (e.g. `validate`) can import it.
os.environ.setdefault("NIXTLA_NUMBA_CACHE", "1")
