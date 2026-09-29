"""中间层 —— 她可以触及、可以修改的部分。

这里放 SelfModel（她的自我认知）、反思回路（她的元认知）、
皮层（她的声音）。这一层的读写就是「她自己」：信念是她改的，
叙事是她写的，特质是她从自己的言行里归纳出来的。
"""

from .self_model import SelfModel
from .cortex import Cortex
from .reflection import Reflection

__all__ = ["SelfModel", "Cortex", "Reflection"]