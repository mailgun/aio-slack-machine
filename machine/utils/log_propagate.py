# -*- coding: utf-8 -*-

import logging

from loguru import logger


class PropagateHandler(logging.Handler):
    def emit(self, record):
        logging.getLogger(record.name).handle(record)


def install():
    logger.add(PropagateHandler(), format="{message}")
