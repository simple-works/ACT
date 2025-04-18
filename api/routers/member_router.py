from discord import Member
from fastapi import APIRouter, HTTPException
from odmantic import query

from bot.main import ActBot
from db.actor import Actor
from utils.log import logger

log = logger(__name__)


# ----------------------------------------------------------------------------------------------------
# * Member Router
# ----------------------------------------------------------------------------------------------------
class MemberRouter(APIRouter):
    def __init__(self, bot: ActBot, *args, **kwargs):
        super().__init__(prefix="/members", tags=["Members"], *args, **kwargs)

        def member_dict(actor: Actor, member: Member):
            return {
                **actor.model_dump(),
                "id": member.id,
                "name": member.name,
                "display_name": member.display_name,
                "display_avatar_url": member.display_avatar.url,
                "display_icon_url": member.display_icon,
                "banner_url": member.banner.url if member.banner else None,
                "created_at": member.created_at,
                "joined_at": member.joined_at,
            }

        # ----------------------------------------------------------------------------------------------------

        @self.get("/{guild_id}", response_model=list[dict])
        async def get_members(guild_id: int, limit: int = 10, top: bool = True):
            try:
                for guild in bot.guilds:
                    if guild.id == guild_id:
                        return [
                            member_dict(actor, member)
                            for actor, member in await (
                                bot.get_actors_members(guild, limit)
                                if top
                                else bot.get_actors_members(guild, limit, None)
                            )
                        ]
                    raise HTTPException(
                        status_code=404,
                        detail=f"No guild with id '{guild_id}' found among the guilds the discord bot is member of.",
                    )
                raise HTTPException(
                    status_code=404,
                    detail="No guilds found. The discord bot is not member of any guild",
                )
            except Exception as e:
                log.exception(e)
                raise HTTPException(status_code=400, detail=str(e))
