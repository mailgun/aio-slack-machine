# -*- coding: utf-8 -*-

import asyncio
import inspect
import logging
import sys
import time
from functools import partial

import dill
from aiohttp.web import Application, AppRunner, TCPSite
from clint.textui import puts, indent, colored

from machine.dispatch import EventDispatcher
from machine.plugins.base import MachineBasePlugin
from machine.settings import import_settings
from machine.singletons import Slack, Scheduler, Storage
from machine.slack import MessagingClient
from machine.storage import PluginStorage
from machine.utils import aio
from machine.utils.module_loading import import_string
from machine.utils.text import show_valid, show_invalid, warn, error, announce

logger = logging.getLogger(__name__)


class Machine:
    def __init__(self, loop=None, settings=None):
        announce("Initializing Slack Machine:")

        with indent(4):
            self._loop = loop or asyncio.get_event_loop()

            puts("Loading settings...")
            if settings:
                self._settings = settings
                found_local_settings = True
            else:
                self._settings, found_local_settings = import_settings()

            fmt = (
                "[%(asctime)s][%(levelname)s] %(name)s %(filename)s:%(funcName)s"
                ":%(lineno)d | %(message)s"
            )
            date_fmt = "%Y-%m-%d %H:%M:%S"
            log_level = self._settings.get("LOGLEVEL", logging.ERROR)
            logging.basicConfig(level=log_level, format=fmt, datefmt=date_fmt)

            if not found_local_settings:
                warn("No local_settings found! Are you sure this is what you want?")

            if "SLACK_API_TOKEN" not in self._settings:
                error("No SLACK_API_TOKEN found in settings! I need that to work...")
                sys.exit(1)

            self._client = Slack(loop=self._loop)

            puts(
                "Initializing storage using backend: {}".format(
                    self._settings["STORAGE_BACKEND"]
                )
            )
            self._storage = Storage.get_instance()
            logger.debug("Storage initialized!")

            self._loop.run_until_complete(self._storage.connect())

            self._plugin_actions = {
                "process": {},
                "listen_to": {},
                "respond_to": {},
                "catch_all": {},
            }

            self._help = {"human": {}, "robot": {}}

            self._http_runner_cleanup = None
            if not self._settings["DISABLE_HTTP"]:
                self._http_app = Application(loop=self._loop)
            else:
                self._http_app = None

            puts("Loading plugins...")
            self.load_plugins()
            logger.debug(
                "The following plugin actions were registered: %s", self._plugin_actions
            )

            self._dispatcher = EventDispatcher(self._plugin_actions, self._settings)

    def load_plugins(self):
        with indent(4):
            logger.debug("PLUGINS: %s", self._settings["PLUGINS"])
            for plugin in self._settings["PLUGINS"]:
                for class_name, cls in import_string(plugin):
                    if (
                        issubclass(cls, MachineBasePlugin)
                        and cls is not MachineBasePlugin
                    ):
                        logger.debug("Found a Machine plugin: {}".format(plugin))
                        storage = PluginStorage(class_name)
                        instance = cls(self._settings, MessagingClient(), storage)

                        missing_settings = self._register_plugin(class_name, instance)
                        if missing_settings:
                            show_invalid(class_name)
                            with indent(4):
                                error_msg = "The following settings are missing: {}".format(
                                    ", ".join(missing_settings)
                                )
                                puts(colored.red(error_msg))
                                puts(colored.red("This plugin will not be loaded!"))
                            del instance
                        else:
                            instance.init(self._http_app)
                            self._loop.run_until_complete(
                                instance.ainit(self._http_app)
                            )
                            show_valid(class_name)

        self._loop.run_until_complete(
            self._storage.set("manual", dill.dumps(self._help))
        )

    async def run(self):
        announce("\nStarting Slack Machine:")
        self._dispatcher.start()

        with indent(4):
            try:
                await aio.join(
                    [
                        self._connect_slack(),
                        self._start_scheduler(),
                        self._start_http_server(),
                        self._start_keepaliver(),
                    ]
                )
            except (KeyboardInterrupt, SystemExit):
                announce("\nSlack Machine shutting down...")
                await self._http_runner.cleanup()

    async def _connect_slack(self):
        # `rtm.start()` will be continuously waited on and will not
        # return unless the connection is closed.
        if not await self._client.rtm.start():
            logger.error("Could not connect to Slack! Aborting...")
            sys.exit(1)

    async def _start_scheduler(self):
        show_valid("Starting scheduler...")
        Scheduler.get_instance().start()

    async def _start_http_server(self):
        if self._http_app is not None:
            show_valid(f"Starting web server on {http_host}:{http_port}...")

            runner = AppRunner(self._http_app)
            await runner.setup()

            http_host = self._settings.get("HTTP_SERVER_HOST", "127.0.0.1")
            http_port = int(self._settings.get("HTTP_SERVER_PORT", 3000))

            site = TCPSite(runner, http_host, http_port)
            await site.start()

            self._http_runner = runner

    async def _start_keepaliver(self):
        interval = self._settings["KEEP_ALIVE"]
        if interval:
            show_valid(f"Starting keepaliver... [Interval: {interval}s]")
            await self._keepaliver(interval)

    def _start_dispatcher(self):
        show_valid("Starting dispatcher...")
        self._dispatcher.start()

    def _register_plugin(self, plugin_class, cls_instance):
        missing_settings = []
        missing_settings.extend(self._check_missing_settings(cls_instance.__class__))
        methods = inspect.getmembers(cls_instance, predicate=inspect.ismethod)
        for _, fn in methods:
            missing_settings.extend(self._check_missing_settings(fn))
        if missing_settings:
            return missing_settings

        if hasattr(cls_instance, "catch_all"):
            self._plugin_actions["catch_all"][plugin_class] = {
                "class": cls_instance,
                "class_name": plugin_class,
                "function": getattr(cls_instance, "catch_all"),
            }
        if cls_instance.__doc__:
            class_help = cls_instance.__doc__.splitlines()[0]
        else:
            class_help = plugin_class
        self._help["human"][class_help] = self._help["human"].get(class_help, {})
        self._help["robot"][class_help] = self._help["robot"].get(class_help, [])
        for name, fn in methods:
            if hasattr(fn, "metadata"):
                self._register_plugin_actions(
                    plugin_class, fn.metadata, cls_instance, name, fn, class_help
                )

    def _check_missing_settings(self, fn_or_class):
        missing_settings = []
        if (
            hasattr(fn_or_class, "metadata")
            and "required_settings" in fn_or_class.metadata
        ):
            for setting in fn_or_class.metadata["required_settings"]:
                if setting not in self._settings:
                    missing_settings.append(setting.upper())
        return missing_settings

    def _register_plugin_actions(
        self, plugin_class, metadata, cls_instance, fn_name, fn, class_help
    ):
        fq_fn_name = "{}.{}".format(plugin_class, fn_name)
        if fn.__doc__:
            self._help["human"][class_help][fq_fn_name] = self._parse_human_help(
                fn.__doc__
            )
        for action, config in metadata["plugin_actions"].items():
            if action == "process":
                event_type = config["event_type"]
                event_handlers = self._plugin_actions["process"].get(event_type, {})
                event_handlers[fq_fn_name] = {
                    "class": cls_instance,
                    "class_name": plugin_class,
                    "function": fn,
                }
                self._plugin_actions["process"][event_type] = event_handlers
            elif action == "respond_to" or action == "listen_to":
                for regex in config["regex"]:
                    event_handler = {
                        "class": cls_instance,
                        "class_name": plugin_class,
                        "function": fn,
                        "regex": regex,
                    }
                    key = "{}-{}".format(fq_fn_name, regex.pattern)
                    self._plugin_actions[action][key] = event_handler
                    self._help["robot"][class_help].append(
                        self._parse_robot_help(regex, action)
                    )
            elif action == "schedule":
                Scheduler.get_instance().add_job(
                    fq_fn_name,
                    trigger="cron",
                    args=[cls_instance],
                    id=fq_fn_name,
                    replace_existing=True,
                    **config,
                )
            elif action == "route":
                for route_config in config:
                    bottle.route(**route_config)(fn)

    @staticmethod
    def _parse_human_help(doc):
        summary = doc.splitlines()[0].split(":")
        if len(summary) > 1:
            command = summary[0].strip()
            cmd_help = summary[1].strip()
        else:
            command = "??"
            cmd_help = summary[0].strip()
        return {"command": command, "help": cmd_help}

    @staticmethod
    def _parse_robot_help(regex, action):
        if action == "respond_to":
            return "@botname {}".format(regex.pattern)
        else:
            return regex.pattern

    async def _keepaliver(self, interval):
        while True:
            await asyncio.sleep(interval)
            await self._client.rtm.ping()
            logger.debug("Client Ping!")
