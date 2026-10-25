"""TechFarmer: a local, private, reference-guided Discord harvesting bot."""

import sys
import sysconfig

__version__ = "0.4.0"
REPOSITORY = "https://github.com/ViaticNovva/TechFarmer"
MIN_PYTHON = (3, 11)
MAX_PYTHON = (3, 15)
PYTHON_NOTICE = "Use stable, final standard CPython 3.11–3.15 (3.15 preferred for a new base install). Alpha/beta/rc previews, free-threaded builds and Python 3.16+ are outside this release's target range. Package preflight is still required."
VISION_PYTHON_NOTICE = (
    "CPython 3.15 optional matching blocker: the latest verified stable PyTorch 2.14.1 publishes no cp315 wheel "
    "(checked 2026-10-25). Matching is NOT claimed supported on 3.15; a compatible stable wheel set and actual imports/API must pass first. "

    "Base no-reference collection does not need Torch. Keep an existing working 3.11–3.14 vision environment "
    "if needed; changing interpreter is a separate explicit action, never an automatic fallback. "
    "No nightly, alternate index, compiler build or fabricated matches are used."
)


def supported_python(version=None, implementation=None, free_threaded=None, releaselevel=None):
    details = sys.version_info if version is None else tuple(version)
    if releaselevel is None:
        releaselevel = details[3] if len(details) > 3 else "final"
    version = tuple(details[:2])
    implementation = sys.implementation.name if implementation is None else implementation
    free_threaded = bool(sysconfig.get_config_var("Py_GIL_DISABLED")) if free_threaded is None else free_threaded
    return implementation == "cpython" and releaselevel == "final" and not free_threaded and MIN_PYTHON <= version <= MAX_PYTHON


def runtime_label():
    version = ".".join(map(str, sys.version_info[:3]))
    if len(sys.version_info) > 3 and sys.version_info[3] != "final":
        version += f" ({sys.version_info[3]}{sys.version_info[4]})"
    return f"{sys.implementation.name} {version} | {sys.executable}"
