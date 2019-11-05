# -*- coding: utf-8 -*-

from machine.singletons import Scheduler, Slack
from machine.utils.aio import run_coro_until_complete


class MessagingClient:
    @property
    def channels(self):
        return run_coro_until_complete(self.get_channels())

    async def get_channels(self):
        return Slack.get_instance().web.channels_list()

    @property
    def users(self):
        return run_coro_until_complete(self.get_users())

    async def get_users(self):
        return Slack.get_instance().web.users_list()

    @staticmethod
    def retrieve_bot_info():
        return Slack.get_instance().login_data.get("self")

    async def fmt_mention(self, user):
        u = (await self.get_users()).find(user)
        return f"<@{u.id}>"

    @staticmethod
    async def send(channel, text, thread_ts=None):
        return await MessagingClient.send_webapi(channel, text, thread_ts=thread_ts)

    def send_scheduled(self, when, channel, text):
        args = [self, channel, text]
        kwargs = {"thread_ts": None}
        Scheduler.get_instance().add_job(
            trigger="date", args=args, kwargs=kwargs, run_date=when
        )

    @staticmethod
    async def send_webapi(
        channel, text, attachments=None, thread_ts=None, ephemeral_user=None
    ):
        method = "chat.postEphemeral" if ephemeral_user else "chat.postMessage"
        kwargs = {
            "channel": channel,
            "text": text,
            "attachments": attachments,
            "as_user": True,
        }

        if ephemeral_user:
            kwargs["user"] = ephemeral_user
        else:
            if thread_ts:
                kwargs["thread_ts"] = thread_ts

        return await Slack.get_instance().api_call(method, **kwargs)

    def send_webapi_scheduled(
        self, when, channel, text, attachments=None, ephemeral_user=None
    ):
        args = [self, channel, text]
        kwargs = {
            "attachments": attachments,
            "ephemeral_user": ephemeral_user,
            "thread_ts": None,
        }

        Scheduler.get_instance().add_job(
            trigger="date", args=args, kwargs=kwargs, run_date=when
        )

    @staticmethod
    async def react(channel, ts, emoji):
        return await Slack.get_instance().api_call(
            "reactions.add", name=emoji, channel=channel, timestamp=ts
        )

    @staticmethod
    async def open_im(user):
        response = await Slack.get_instance().api_call("im.open", user=user)
        return response["channel"]["id"]

    async def send_dm(self, user, text):
        u = (await self.get_users()).find(user)
        dm_channel = await self.open_im(u.id)
        return await self.send(dm_channel, text)

    def send_dm_scheduled(self, when, user, text):
        args = [self, user, text]
        Scheduler.get_instance().add_job(
            MessagingClient.send_dm, trigger="date", args=args, run_date=when
        )

    async def send_dm_webapi(self, user, text, attachments=None):
        u = (await self.get_users()).find(user)
        dm_channel = await self.open_im(u.id)
        return await Slack.get_instance().api_call(
            "chat.postMessage",
            channel=dm_channel,
            text=text,
            attachments=attachments,
            as_user=True,
        )

    def send_dm_webapi_scheduled(self, when, user, text, attachments=None):
        args = [self, user, text]
        kwargs = {"attachments": attachments}
        Scheduler.get_instance().add_job(
            MessagingClient.send_dm_webapi,
            trigger="date",
            args=args,
            kwargs=kwargs,
            run_date=when,
        )
