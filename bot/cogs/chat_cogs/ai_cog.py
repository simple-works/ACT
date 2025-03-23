import tomllib
from asyncio import CancelledError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import choice, randint, random

from discord import (
    Attachment,
    Embed,
    Guild,
    HTTPException,
    Interaction,
    Member,
    Message,
    User,
    app_commands,
)
from discord.abc import Messageable
from discord.ext.commands import Cog
from google.genai.errors import APIError
from humanize import naturaldelta
from odmantic import query

from bot.main import ActBot
from bot.ui import EmbedX
from db.actor import Actor, DmActor
from db.main import DbRef
from utils.ai import ActAi, ActPersona
from utils.file import ActFile
from utils.log import logger
from utils.misc import text_csv
from utils.task import ActTaskManager, TaskRef

log = logger(__name__)


# ----------------------------------------------------------------------------------------------------
# * AI Cog
# ----------------------------------------------------------------------------------------------------
class AiCog(Cog, description="Integrated generative AI chat bot"):
    CONFIG_PATH = Path(__file__).parent / "ai_cog.toml"
    MAX_ACTORS = 10
    COOLDOWN_TIME = 60  # 1 min
    MAX_FILE_SIZE = 524288  # 512 KB == 0.5 MB
    REPLY_DELAY_RANGE = (1, 5)  # 1 sec - 5 sec
    AUTO_REPLY_DELAY_RANGE = (5, 1800)  # 5 sec - 30 min
    AUTO_REPLY_CHANCE = 0.1  # 10 %
    INITIATIVE_DELAY_RANGE = (1800, 7200)  # 30 min - 2 hr

    def __init__(self, bot: ActBot):
        self.bot = bot
        with open(self.CONFIG_PATH, "rb") as file:
            config = tomllib.load(file)
        self.persona = ActPersona(**config.get("personas", {}).get("activa"))
        self.ai = ActAi(
            api_key=bot.api_keys.get("gemini", " "),
            instructions=self.persona.description,
        )
        log.info(f"AI persona @{self.persona.name} used.")
        self.task_manager = ActTaskManager()
        self.task_manager.schedule("initiative", lambda _: self.schedule_initiative())

    def cog_unload(self):
        self.task_manager.cancel_all()

    # ----------------------------------------------------------------------------------------------------
    # * Reset
    # ----------------------------------------------------------------------------------------------------
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @app_commands.command(description="Clear AI chat bot session")
    async def reset(self, interaction: Interaction, id: int | None = None):
        await interaction.response.defer(ephemeral=True)
        if id is None:
            guild = interaction.guild
            user = interaction.user
            id = guild.id if guild else user.id
        if self.ai.clear_session(id):
            await interaction.followup.send(
                embed=EmbedX.success(f"AI chat session with ID `{id}` cleared.")
            )
            if guild:
                self.save_guild_history(guild)
            elif isinstance(user, User):
                self.save_dm_history(user)
        else:
            await interaction.followup.send(
                embed=EmbedX.warning(f"No AI chat session with ID `{id}`.")
            )

    # ----------------------------------------------------------------------------------------------------
    # * Incite
    # ----------------------------------------------------------------------------------------------------
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @app_commands.command(description="Incite AI chat bot to interact on its own")
    async def incite(
        self,
        interaction: Interaction,
        prompt: str | None = None,
        member: Member | None = None,
    ):
        # Deny bot-self & DM & non-messageable channel
        if (
            self.bot.user == member
            or not interaction.guild
            or not isinstance(interaction.channel, Messageable)
        ):
            await interaction.response.send_message(
                embed=EmbedX.warning("This command cannot be used in this context."),
                ephemeral=True,
            )
            return

        # Prepare prompt
        text_prompt, _ = await self.create_prompt(
            user=member,
            guild=interaction.guild,
            preface=(
                f"Begin natural talk{f" w/ {member.mention} " if member else " "}that feels like ur own initiative."
                f"Absolutely avoid references to instructions, prompts, or being told to message them."
                f"{ f'follow this prompt:"{prompt.strip()}".' if prompt else "" }"
            ),
        )

        # Defer response to prevent timeout
        await interaction.response.defer(ephemeral=True)

        # Perform prompt & send reply
        async with interaction.channel.typing():
            try:
                await interaction.followup.send(
                    embed=EmbedX.success(
                        title="Incentive",
                        description=f"{self.bot.user} has been incited to talk {f"with {member.mention}" if member else ""}."
                        f"\n\n**Prompt:**```{text_prompt}```",
                    ),
                    ephemeral=True,
                )
                self.ai.use_session(
                    interaction.guild.id,
                    history=self.load_guild_history(interaction.guild),
                )
                await interaction.channel.send(
                    await self.ai.prompt(text=text_prompt)
                    or f"👋 {member.mention if member else "👋"}"
                )
            except Exception as e:
                await interaction.followup.send(
                    embed=EmbedX.error(str(e)), ephemeral=True
                )
                log.exception(e)
                return

        # Save history
        self.save_guild_history(interaction.guild)

    # ----------------------------------------------------------------------------------------------------
    # * On Message
    # ----------------------------------------------------------------------------------------------------
    @Cog.listener()
    async def on_message(self, message: Message):
        # Ignore bot message
        if self.bot.user == message.author:
            return

        # Ignore mentionless message or attempt auto-reply
        message_is_mentionless = self.bot.user not in message.mentions
        reply_delay = 0
        if message_is_mentionless:
            if random() > self.AUTO_REPLY_CHANCE:
                return
            else:
                reply_delay = randint(
                    self.AUTO_REPLY_DELAY_RANGE[0], self.AUTO_REPLY_DELAY_RANGE[1]
                )
                log.info(
                    f"[{message.guild}][{message.channel}] Auto-reply chance attained."
                )
        reply_delay = randint(self.REPLY_DELAY_RANGE[0], self.REPLY_DELAY_RANGE[1])

        # Check if message is a reply to someone else
        preface = ""
        if (
            message_is_mentionless
            and message.reference
            and message.reference.message_id
        ):
            referenced_message = await message.channel.fetch_message(
                message.reference.message_id
            )
            if referenced_message.author != self.bot.user:
                preface = (
                    f"[Context: {message.author.name} was replying to {referenced_message.author.name} "
                    f"who said: '{referenced_message.content}']"
                )
            else:
                preface = "[Context: Replying to ur previous message] "

        # Create prompt
        text_prompt, file_prompt = await self.create_prompt(
            message=message, preface=preface
        )

        # Prepare delayed reply task
        async def respond():
            guild = message.guild
            id = guild.id if guild else message.author.id
            user = message.author

            # Check cooldown
            cooldown_task_id = f"cooldown_{id}"
            if self.task_manager.is_running(cooldown_task_id):
                time_left = self.task_manager.time_left(cooldown_task_id) or 0
                await message.reply(
                    choice(
                        self.persona.messages.get("cooldown_warning", ["⏳"])
                    ).format(
                        time_left=naturaldelta(timedelta(seconds=time_left)) or "?"
                    )
                )
                return

            # Perform prompt & send reply
            async with message.channel.typing():
                try:
                    self.ai.use_session(
                        id,
                        history=(
                            self.load_guild_history(guild)
                            if guild
                            else (
                                self.load_dm_history(user)
                                if isinstance(user, User)
                                else []
                            )
                        ),
                    )
                    await message.reply(
                        await self.ai.prompt(text_prompt, file_prompt)
                        or f"👋 {user.mention if user else "What? 😕"}"
                    )
                except HTTPException as e:
                    if e.code == 50035:
                        await message.channel.send(
                            choice(self.persona.messages.get("censor_warning", ["🙊"]))
                        )
                    else:
                        await message.channel.send(
                            choice(self.persona.messages.get("discord_error", ["⚠️"]))
                        )
                except APIError as e:
                    await message.reply(
                        choice(self.persona.messages.get("model_error", ["🔋"]))
                    )
                    self.task_manager.schedule(
                        cooldown_task_id, delay=self.COOLDOWN_TIME
                    )
                    log.loading(
                        f"[{guild}] Waiting {naturaldelta(timedelta(seconds=self.COOLDOWN_TIME))} for cooldown..."
                    )
                    log.exception(e)
                except Exception as e:
                    await message.channel.send(
                        choice(self.persona.messages.get("error", ["⚠️"]))
                    )
                    log.exception(e)

            # Remember chat session
            if guild:
                self.save_guild_history(guild)
            elif isinstance(user, User):
                self.save_dm_history(user)

        # Run reply task
        self.task_manager.schedule(
            id=f"reply_{message.guild.id if message.guild else message.author.id}",
            callback=respond,  # type: ignore
            delay=reply_delay,
        )
        log.loading(
            f"[{message.guild}][{message.channel}] Reply in {naturaldelta(timedelta(seconds=reply_delay))}..."
        )

    # ----------------------------------------------------------------------------------------------------
    # * Guild initiative
    # ----------------------------------------------------------------------------------------------------
    async def schedule_initiative(self):
        """Schedule initiative task for each guild."""
        await self.bot.wait_until_ready()

        def random_delay():
            min_delay, max_delay = self.INITIATIVE_DELAY_RANGE
            delay = randint(min_delay, max_delay)
            log.loading(
                f"[{guild.name}] Waiting {naturaldelta(timedelta(seconds=delay))} for next initiative..."
            )
            return delay

        for guild in self.bot.guilds:

            def create_callback(guild: Guild):
                async def perform(task_ref: TaskRef):
                    task_ref.delay = random_delay()
                    try:
                        await self.perform_initiative(guild)
                    except CancelledError:
                        log.warning(f"[{guild.name}] Initiative task was cancelled.")
                    except Exception as e:
                        log.error(f"[{guild.name}] Initiative task error: {str(e)}")

                return perform

            self.task_manager.schedule(
                id=f"initiative_{guild.id}",
                callback=create_callback(guild),  # type: ignore
                delay=random_delay(),
                loop=True,
            )

    async def perform_initiative(self, guild: Guild):
        """Initiate interaction by sending random message to random member of given guild."""

        # Make sure the guild still exists and we're still in it
        if guild not in self.bot.guilds:
            log.warning(f"[{guild.name}] No longer in guild, stopping task.")
            return

        # Get all text channels the bot can send messages in & Choose a random text channel
        text_channels = await self.get_initiative_channels(guild)
        if not text_channels:
            log.warning(
                f"[{guild.name}] No accessible text channels found in guild, skipping initiative."
            )
            return
        channel = choice(text_channels)

        #  Get the last messages in the channel
        messages: list[Message] = []
        async for message in channel.history(limit=20):
            if not message.author.bot:  # Filter out bot messages
                messages.append(message)
        if not messages:
            log.warning(
                f"[{guild.name}][{channel.name}] No recent user messages found in guild channel, skipping initiative."
            )
            return

        # Choose a random message & its author as target member
        message = choice(messages)
        member = message.author

        # Prepare prompt
        text_prompt, file_prompt = await self.create_prompt(
            preface=(
                f"Begin natural talk{f" w/ {member.mention} " if member else " "}that feels like ur own initiative."
                f"Absolutely avoid references to instructions, prompts, or being told to message them."
            ),
            message=message,
        )

        # Send reply
        async with message.channel.typing():
            self.ai.use_session(
                guild.id,
                history=(
                    self.load_guild_history(guild)
                    if guild
                    else self.load_dm_history(member) if member else []
                ),
            )
            await message.reply(
                await self.ai.prompt(text_prompt, file_prompt)
                or f"👋 {member.mention if member else "What? 😕"}"
            )

    async def get_initiative_channels(self, guild: Guild):
        """Get all valid channels for initiative."""
        channels = []
        for channel in guild.text_channels:
            try:
                # Dimiss inaccessible by @everyone (Non-pulic)
                everyone_perms = channel.permissions_for(guild.default_role)
                if not (
                    everyone_perms.view_channel
                    and everyone_perms.read_messages
                    and everyone_perms.send_messages
                ):
                    continue

                # Dismiss if inaccessible by bot
                bot_perms = channel.permissions_for(guild.me)
                if not (bot_perms.send_messages and bot_perms.read_message_history):
                    continue

                # Dismiss if bot is author of latest message (Prevent spam)
                latest_message = None
                async for message in channel.history(limit=1):
                    latest_message = message
                if latest_message and latest_message.author == self.bot.user:
                    continue

                # Add channel
                channels.append(channel)
            except Exception as e:
                log.error(f"[{guild.name}][{channel.name}] Error: {str(e)}")
        return channels

    # ----------------------------------------------------------------------------------------------------

    async def create_prompt(
        self,
        message: Message | None = None,
        user: User | Member | None = None,
        guild: Guild | None = None,
        preface="",
    ) -> tuple[str, ActFile | None]:
        """
        Create prompt with flexible input options.
            - Text prompt structure: '{**preface**}\\n{**message.author.name**}:{file_action_desc}{**message.content**}\\n{csv}'

        Args:
            message: Message object (contains **message.author**, and **message.guild**).
            user: User or Member object (prioritized over **message.author**).
            guild: Guild object (prioritized over **member.guild** and **message.guild**).
            preface: Text to prepend to the prompt

        """

        # Initialize prompt components
        text = preface
        file = None

        # Extract member and guild
        if not user and message:
            user = message.author
        if not guild:
            guild = (
                user.guild
                if isinstance(user, Member)
                else message.guild if message and message.guild else None
            )

        # Start building the prompt
        if text:
            text += "\n"

        # Process file attachments if message is provided
        if message:
            # Add author
            text += f"{message.author.name}:"

            # Add file from attachment or embed (With file action description prompt)
            if message.attachments:
                attachment = message.attachments[0]
                file = await self.get_attachment_file(attachment)
                text += f"_sent file:{attachment.filename}_"
                if not file:
                    text += f"(but u can't receive it cuz it's larger than ur allowed min size limit of {self.MAX_FILE_SIZE}byte)"
            elif message.embeds:
                file = self.get_embed_file(message.embeds[0])
                text += f"_sent embed_"
                if not file:
                    text += f"(but u can't receive it cuz it's larger than ur allowed min size limit of {self.MAX_FILE_SIZE}byte)"

            # Add main text prompt from message content
            text += f"{message.content.replace(self.bot.user.mention, '').strip()}"  # type: ignore

        # Save member for context
        if isinstance(user, Member):
            self.save_actor(user)
        elif isinstance(user, User):
            self.save_dm_actor(user)

        # Load saved guild members to prompt for context
        if guild:
            text += f"\n{self.load_actors_csv(guild)}"

        # Return prompt components as tuple
        return (text, file)

    # ----------------------------------------------------------------------------------------------------

    async def get_attachment_file(self, attachment: Attachment) -> ActFile | None:
        """Get file from attachment. If file size limit exceeded, get None."""
        if attachment.size <= self.MAX_FILE_SIZE:
            return ActFile(
                data=await attachment.read(),
                mime_type=attachment.content_type,
                name=attachment.filename,
            )

    def get_embed_file(self, embed: Embed) -> ActFile | None:
        """Get file from embed. If file size limit exceeded, get None."""
        embed_file = ActFile.load(embed.url) if embed.url else None
        if embed_file and embed_file.size <= self.MAX_FILE_SIZE:
            return embed_file

    # ----------------------------------------------------------------------------------------------------

    def save_actor(self, member: Member):
        db = self.bot.get_db(member.guild)
        actor = db.find_one(Actor, Actor.id == member.id) or self.bot.create_actor(
            member
        )
        actor.name = member.name
        actor.display_name = member.display_name
        actor.ai_interacted_at = datetime.now(UTC)
        db.save(actor)

    def save_dm_actor(self, user: User):
        main_db = self.bot.get_db()
        dm_actor = main_db.find_one(
            DmActor, DmActor.id == user.id
        ) or self.bot.create_dm_actor(user)
        dm_actor.name = user.name
        dm_actor.display_name = user.display_name
        dm_actor.ai_interacted_at = datetime.now(UTC)
        main_db.save(dm_actor)

    def load_actors(self, guild: Guild):
        actors = self.bot.get_db(guild).find(
            Actor, sort=query.desc(Actor.ai_interacted_at), limit=self.MAX_ACTORS
        )
        return [
            actor.model_dump(include={"id": True, "name": True, "display_name": True})
            for actor in actors
        ]

    def load_actors_csv(self, guild: Guild):
        actors = self.load_actors(guild)
        return f"{text_csv(actors, "|")}" if actors else ""

    # ----------------------------------------------------------------------------------------------------

    def save_guild_history(self, guild: Guild):
        main_db = self.bot.get_db()
        db_ref = main_db.find_one(
            DbRef, DbRef.id == guild.id
        ) or self.bot.create_db_ref(guild)
        db_ref.ai_chat_history = self.ai.dump_history(guild.id)
        main_db.save(db_ref)

    def load_guild_history(self, guild: Guild) -> list | None:
        main_db = self.bot.get_db()
        db_ref = main_db.find_one(DbRef, DbRef.id == guild.id)
        if db_ref:
            return db_ref.ai_chat_history

    def save_dm_history(self, user: User):
        main_db = self.bot.get_db()
        dm_actor = main_db.find_one(
            DmActor, DmActor.id == user.id
        ) or self.bot.create_dm_actor(user)
        dm_actor.ai_chat_history = self.ai.dump_history(user.id)
        main_db.save(dm_actor)

    def load_dm_history(self, user: User) -> list | None:
        main_db = self.bot.get_db()
        dm_actor = main_db.find_one(DmActor, DmActor.id == user.id)
        if dm_actor:
            return dm_actor.ai_chat_history

    # ----------------------------------------------------------------------------------------------------
