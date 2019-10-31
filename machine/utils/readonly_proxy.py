# -*- coding: utf-8 -*-


class ReadonlyProxy:
    __slots__ = "_target"

    def __init__(self, target):
        self._target = target

    def __getattr__(self, item):
        return getattr(self._target, item)

