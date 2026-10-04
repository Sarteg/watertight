"""watertight: repair STL files for 3D printing."""

__version__ = "0.1.0"

from .core.check import CheckResult, check_file  # noqa: E402
from .core.options import RepairOptions  # noqa: E402
from .core.pipeline import repair  # noqa: E402
from .core.report import RepairResult  # noqa: E402

__all__ = ["__version__", "check_file", "repair", "CheckResult", "RepairResult", "RepairOptions"]
