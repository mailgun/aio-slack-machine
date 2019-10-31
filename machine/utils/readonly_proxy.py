# -*- coding: utf-8 -*-

from typing import Any, Generic, T


class ReadonlyProxy(Generic[T]):
    __slots__ = "_target"

    def __init__(self, target: T):
        self._target = target

    def __getattr__(self, item: str) -> Any:
        return getattr(self._target, item)
