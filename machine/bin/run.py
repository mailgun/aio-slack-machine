# -*- coding: utf-8 -*-

import asyncio
import sys
import os

from machine import Machine
from machine.utils.text import announce


def main():
    loop = asyncio.new_event_loop()

    # When running this function as console entry point, the current working dir is not in the
    # Python path, so we have to add it
    sys.path.insert(0, os.getcwd())

    bot = Machine(loop=loop)
    # try:
    loop.run_until_complete(bot.run())
    # except KeyboardInterrupt:
    announce("Thanks for playing!")
    loop.stop()
    sys.exit(0)
