import sys

ver = sys.version_info[:2]

from typing import TYPE_CHECKING

__all__ = ['ParamSpec', 'Self']

if TYPE_CHECKING:  # pragma: no cover
    from typing import ParamSpec, Self
else:
    Self = None

    # class _Concatenate:
    #     def __getitem__(self, *args):
    #         return []

    # Concatenate = _Concatenate()

    class ParamSpec(list):
        args = None
        kwargs = None

        def __init__(self, *args, **kwargs): ...
