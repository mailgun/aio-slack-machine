# -*- coding: utf-8 -*-

import asyncio
import time
import re
import logging

from machine.singletons import Slack
from machine.utils.pool import ThreadPool
from machine.plugins.base import Message
from machine.slack import MessagingClient

logger = logging.getLogger(__name__)


class EventDispatcher:
    def __init__(self, plugin_actions, settings=None):
        self._client = Slack.get_instance()
        self._plugin_actions = plugin_actions
        self._pool = ThreadPool()
        alias_regex = ""
        if settings and "ALIASES" in settings:
            logger.info("Setting aliases to {}".format(settings["ALIASES"]))
            alias_regex = "|(?P<alias>{})".format(
                "|".join([re.escape(s) for s in settings["ALIASES"].split(",")])
            )
        self.RESPOND_MATCHER = re.compile(
            r"^(?:<@(?P<atuser>\w+)>:?|(?P<username>\w+):{}) ?(?P<text>.*)$".format(
                alias_regex
            ),
            re.DOTALL,
        )

    def start(self):
        self._client.rtm.on(event="message", callback=self.handle_event)
        self._client.rtm.on(event="open", callback=self.handle_event)

    async def handle_event(self, *, data: dict, **kwargs):
        # Gotta catch 'em all!
        await asyncio.gather(
            action["function"](data) for action in self._find_listeners("catch_all")
        )

        # Basic dispatch based on event type
        if "type" in data:
            if data["type"] in self._plugin_actions["process"]:
                handlers = []
                for action in self._plugin_actions["process"][data["type"]].values():
                    handlers.append(action["function"](data))

                await asyncio.gather(*handlers)

        # Handle message listeners
        if "type" in data and data["type"] == "message":
            respond_to_msg = self._check_bot_mention(data)
            if respond_to_msg:
                listeners = self._find_listeners("respond_to")
                await self._dispatch_listeners(listeners, respond_to_msg)
            else:
                listeners = self._find_listeners("listen_to")
                await self._dispatch_listeners(listeners, data)

        if "type" in data and data["type"] == "pong":
            logger.debug("Server Pong!")

    def _find_listeners(self, type):
        return [action for action in self._plugin_actions[type].values()]

    @staticmethod
    def _gen_message(event, plugin_class_name):
        return Message(MessagingClient(), event, plugin_class_name)

    def _get_bot_id(self):
        return self._client.server.login_data["self"]["id"]

    def _get_bot_name(self):
        return self._client.server.login_data["self"]["name"]

    def _check_bot_mention(self, event):
        full_text = event.get("text", "")
        channel = event["channel"]
        bot_name = self._get_bot_name()
        bot_id = self._get_bot_id()
        m = self.RESPOND_MATCHER.match(full_text)

        if channel[0] == "C" or channel[0] == "G":
            if not m:
                return None

            matches = m.groupdict()

            atuser = matches.get("atuser")
            username = matches.get("username")
            text = matches.get("text")
            alias = matches.get("alias")

            if alias:
                atuser = bot_id

            if atuser != bot_id and username != bot_name:
                # a channel message at other user
                return None

            event["text"] = text
        else:
            if m:
                event["text"] = m.groupdict().get("text", None)
        return event

    async def _dispatch_listeners(self, listeners, event):
        handlers = []
        for l in listeners:
            matcher = l["regex"]
            match = matcher.search(event.get("text", ""))
            if match:
                message = self._gen_message(event, l["class_name"])
                handlers.append(l["function"](message, **match.groupdict()))

        if handlers:
            await asyncio.gather(*handlers)
