from .resvglite import *  # re-export the compiled submodule

__doc__ = resvglite.__doc__
if hasattr(resvglite, "__all__"):
    __all__ = resvglite.__all__
