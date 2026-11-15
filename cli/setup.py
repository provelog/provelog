"""Build shim: carry the repo-root web/ assets into the package.

Metadata lives in pyproject.toml; the only thing this file adds is a
build step. The browser verifier's static assets (web/verify.js, the
conformance page, ...) sit at the repository root next to the sources
they are conformance-tested against, but a wheel only carries what is
inside the package. The build copies web/ into provelog/web/ verbatim -
file contents are never rewritten - and server.app.read_web_asset serves
the package copy first, the repository layout second.

MANIFEST.in grafts web/ into the sdist, so a wheel built FROM the sdist
finds the same files at its root and this same copy step runs.
"""

from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

_WEB_DIR = "web"                                                     
_PACKAGE = "provelog"


class _BuildPyWithWebAssets(build_py):
    """build_py + web/** copied into build/lib/provelog/web/, contents
    verbatim. Runs for wheels and for `pip install .` alike; a missing
    web/ (building from something that is not this repo tree) is not an
    error - the rest of the package builds exactly as before."""

    def run(self):
        super().run()
        src = Path(__file__).parent / _WEB_DIR
        if not src.is_dir():
            return
        target = Path(self.build_lib) / _PACKAGE / _WEB_DIR
        self.copy_tree(str(src), str(target))
                                                                          
                                                                        


setup(cmdclass={"build_py": _BuildPyWithWebAssets})
