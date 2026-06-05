# from collections import defaultdict
# from datetime import timedelta

# from discord import Member, Message
# from discord.ext.commands import Cog
# from humanize import naturaltime, precisedelta
# from profanity_check import predict_prob

# from bot.main import ActBot
# from bot.ui.embed import EmbedX
# from db.actor import Actor
# from utils.log import logger

# log = logger(__name__)


# # ----------------------------------------------------------------------------------------------------
# # * Filter Cog
# # ----------------------------------------------------------------------------------------------------
# class FilterCog(Cog, description="Filter blacklisted message content."):
#     TOLERANCE = 0.99  # 99 %
#     MAX_OFFENSES = 5
#     GOLD_PENALTY = 500

#     def __init__(self, bot: ActBot):
#         self.bot = bot
#         self.offenses = defaultdict(int)  # { user_id : offense_count }

#     # ----------------------------------------------------------------------------------------------------
#     # * On Message
#     # ----------------------------------------------------------------------------------------------------
#     @Cog.listener()
#     async def on_message(self, message: Message):
#         # Ignore DM & bot messages
#         member = message.author
#         if not message.guild or not isinstance(member, Member) or member.bot:
#             return

#         # Identify profane words
#         profane_words = self.get_profane_words(message.content.split())
#         if not profane_words:
#             return

#         # Censor message content
#         censored_content = message.content
#         for word in profane_words:
#             censored_content = censored_content.replace(word, f"||{word}||")

#         # Delete & replace message
#         await message.delete()
#         embed = EmbedX.error(censored_content, "", "")
#         embed.add_field(name="", value="")
#         embed.set_author(name=member.display_name, icon_url=member.avatar)
#         embed.set_footer(text="🚫 Censored Message")
#         censored_message = await message.channel.send(embed=embed)

#         # Accumulate offenses to detect abuse
#         self.offenses[member.id] += 1
#         if self.offenses[member.id] < FilterCog.MAX_OFFENSES:
#             return

#         # Penalize by gold
#         db = self.bot.get_db(message.guild)
#         actor = db.find_one(Actor, Actor.id == member.id)
#         if not actor:
#             actor = self.bot.create_actor(member)
#         debt_gold = 0
#         if actor.gold >= self.GOLD_PENALTY:
#             actor.gold -= self.GOLD_PENALTY
#         else:
#             debt_gold = self.GOLD_PENALTY - actor.gold
#             actor.gold = 0
#         db.save(actor)
#         self.offenses[member.id] = 0

#         # Penalize by timeout (if insufficient gold)
#         time = 0
#         if debt_gold:
#             time = timedelta(seconds=int(0.5 * debt_gold))  # 0.5 seconds per gold
#             if not member.guild_permissions.administrator:
#                 await member.timeout(time, reason="Repeated use of offensive language")

#         # Notice
#         embed = EmbedX.error(
#             emoji="🚨",
#             title="Penalty",
#             description=f"{member.mention} has been penalized for repeated use of offensive language.",
#         )
#         embed.add_field(name="Gold 🔻", value=f"💰 **-{self.GOLD_PENALTY}**")
#         if debt_gold:
#             embed.add_field(
#                 name=f"Timeout",
#                 value=f"⏳ **{precisedelta(time)}**\n-# **💰 {debt_gold}** Debt Converted",
#             )
#         embed.set_thumbnail(url=member.display_avatar.url)
#         await censored_message.reply(
#             content=f"Sorry, {member.mention}! 💀", embed=embed
#         )

#     # ----------------------------------------------------------------------------------------------------

#     @classmethod
#     def get_profane_words(cls, words: list[str]) -> list[str] | None:
#         """Get list of profane words from given list. If non found, get empty list."""
#         predictions = predict_prob(words) if words else None
#         if predictions is None:
#             return None
#         profane_words = []
#         for i, word in enumerate(words):
#             if predictions[i] >= cls.TOLERANCE:  # 1 means profane, 0 means clean
#                 profane_words.append(word)
#         return profane_words
