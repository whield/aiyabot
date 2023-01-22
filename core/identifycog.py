import base64
import discord
import traceback
import requests
from asyncio import AbstractEventLoop
from discord import option
from discord.ext import commands
from threading import Thread
from typing import Optional

from core import queuehandler
from core import viewhandler
from core import settings


class IdentifyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.slash_command(name='identify', description='Describe an image', guild_only=True)
    @option(
        'init_image',
        discord.Attachment,
        description='The image to identify.',
        required=False,
    )
    @option(
        'init_url',
        str,
        description='The URL image to identify. This overrides init_image!',
        required=False,
    )
    @option(
        'phrasing',
        str,
        description='The way the image will be described.',
        required=False,
        choices=['Normal', 'Tags']
    )
    async def dream_handler(self, ctx: discord.ApplicationContext, *,
                            init_image: Optional[discord.Attachment] = None,
                            init_url: Optional[str],
                            phrasing: Optional[str] = 'Normal'):

        has_image = True
        # url *will* override init image for compatibility, can be changed here
        if init_url:
            try:
                init_image = requests.get(init_url)
            except(Exception,):
                await ctx.send_response('URL image not found!\nI have nothing to work with...', ephemeral=True)
                has_image = False

        # fail if no image is provided
        if init_url is None:
            if init_image is None:
                await ctx.send_response('I need an image to identify!', ephemeral=True)
                has_image = False

        # Update layman-friendly "phrasing" choices into what API understands
        if phrasing == 'Normal':
            phrasing = 'clip'
        else:
            phrasing = 'deepdanbooru'

        view = viewhandler.DeleteView(ctx.author.id)
        # set up the queue if an image was found
        if has_image:
            if queuehandler.GlobalQueue.dream_thread.is_alive():
                user_already_in_queue = False
                for queue_object in queuehandler.GlobalQueue.queue:
                    if queue_object.ctx.author.id == ctx.author.id:
                        user_already_in_queue = True
                        break
                if user_already_in_queue:
                    await ctx.send_response(content=f'Please wait! You\'re queued up.', ephemeral=True)
                else:
                    queuehandler.GlobalQueue.queue.append(queuehandler.IdentifyObject(self, ctx, init_image, phrasing, view))
                    await ctx.send_response(
                        f"<@{ctx.author.id}>, I'm identifying the image!"
                        f"\nQueue: ``{len(queuehandler.GlobalQueue.queue)}``", delete_after=45.0)
            else:
                await queuehandler.process_dream(self, queuehandler.IdentifyObject(self, ctx, init_image, phrasing, view))
                await ctx.send_response(
                    f"<@{ctx.author.id}>, I'm identifying the image!"
                    f"\nQueue: ``{len(queuehandler.GlobalQueue.queue)}``", delete_after=45.0)

    # the function to queue Discord posts
    def post(self, event_loop: AbstractEventLoop, post_queue_object: queuehandler.PostObject):
        event_loop.create_task(
            post_queue_object.ctx.channel.send(
                content=post_queue_object.content,
                embed=post_queue_object.embed,
                view=post_queue_object.view
            )
        )
        if queuehandler.GlobalQueue.post_queue:
            self.post(self.event_loop, self.queue.pop(0))

    def dream(self, event_loop: AbstractEventLoop, queue_object: queuehandler.IdentifyObject):
        try:
            # construct a payload
            image = base64.b64encode(requests.get(queue_object.init_image.url, stream=True).content).decode('utf-8')
            payload = {
                "image": 'data:image/png;base64,' + image,
                "model": queue_object.phrasing
            }

            # send normal payload to webui
            with requests.Session() as s:
                if settings.global_var.api_auth:
                    s.auth = (settings.global_var.api_user, settings.global_var.api_pass)

                if settings.global_var.gradio_auth:
                    login_payload = {
                        'username': settings.global_var.username,
                        'password': settings.global_var.password
                    }
                    s.post(settings.global_var.url + '/login', data=login_payload)
                else:
                    s.post(settings.global_var.url + '/login')

                response = s.post(url=f'{settings.global_var.url}/sdapi/v1/interrogate', json=payload)
            response_data = response.json()

            # post to discord
            def post_dream():
                embed = discord.Embed()
                embed.set_image(url=queue_object.init_image.url)
                embed.colour = settings.global_var.embed_color
                embed.add_field(name=f'I think this is', value=f'``{response_data.get("caption")}``', inline=False)

                footer_args = dict(text=f'{queue_object.ctx.author.name}#{queue_object.ctx.author.discriminator}')
                if queue_object.ctx.author.avatar is not None:
                    footer_args['icon_url'] = queue_object.ctx.author.avatar.url
                embed.set_footer(**footer_args)

                queuehandler.process_post(
                    self, queuehandler.PostObject(
                        self, queue_object.ctx, content=f'<@{queue_object.ctx.author.id}>', file='', files='', embed=embed, view=queue_object.view))
            Thread(target=post_dream, daemon=True).start()

        except Exception as e:
            embed = discord.Embed(title='identify failed', description=f'{e}\n{traceback.print_exc()}',
                                  color=settings.global_var.embed_color)
            event_loop.create_task(queue_object.ctx.channel.send(embed=embed))
        # check each queue for any remaining tasks
        queuehandler.process_queue()


def setup(bot):
    bot.add_cog(IdentifyCog(bot))
