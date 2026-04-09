import sys
from typing import TYPE_CHECKING

ver = sys.version_info[:2]

__all__ = ['Concatenate', 'ParamSpec', 'Self']

if TYPE_CHECKING:  # pragma: no cover
    from typing import Concatenate, ParamSpec, Self
else:
    Self = None

    class _Concatenate:
        def __getitem__(self, *args):  # pragma: no cover
            return []

    Concatenate = _Concatenate()

    class ParamSpec(list):
        args = None
        kwargs = None

        def __init__(self, *args, **kwargs): ...
