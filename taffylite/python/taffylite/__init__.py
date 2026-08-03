from .taffylite import *  # re-export the compiled submodule

__doc__ = taffylite.__doc__
if hasattr(taffylite, "__all__"):
    __all__ = taffylite.__all__
