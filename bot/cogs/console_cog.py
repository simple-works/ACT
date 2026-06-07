from discord import (
    Attachment,
    Forbidden,
    HTTPException,
    Interaction,
    Member,
    Message,
    NotFound,
    Object,
    Role,
    TextChannel,
    Thread,
    User,
    VoiceChannel,
    app_commands,
)
from discord.ext.commands import Cog
from discord.utils import MISSING

from bot.main import ActBot
from bot.ui.embed import EmbedX
from bot.ui.modal import TextParagraphModal
from db.actor import Actor
from db.main import DbRef
from utils.log import logger

log = logger(__name__)


# ----------------------------------------------------------------------------------------------------
# * Console Cog
# ----------------------------------------------------------------------------------------------------
class ConsoleCog(Cog, description="Provide control and management interface"):
    def __init__(self, bot: ActBot):
        self.bot = bot

    # ----------------------------------------------------------------------------------------------------
    # * Patch
    # ----------------------------------------------------------------------------------------------------
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(
        description="Patch database records", extras={"category": "Console"}
    )
    async def migrate_data(self, interaction: Interaction):
        return await interaction.response.send_message(
            embed=EmbedX.info("No patch currently.")
        )
        db_api = self.bot._db
        if not db_api:
            log.error("No database api available")
        main_db_engine = db_api.get_engine()
        db_refs = main_db_engine.find(DbRef)
        for db_ref in db_refs:
            db_engine = db_api.get_engine(db_ref.id)
            if not db_engine:
                continue
            raw_actors = db_engine.database[Actor.__collection__].find()
            unset_fields = {"equipment": 1, "equipped_items": 1, "item_stacks": 1}
            for raw_actor in raw_actors:
                actor_id = raw_actor.get("_id")
                db_engine.database[Actor.__collection__].update_one(
                    {"_id": actor_id}, {"$unset": unset_fields}
                )
                log.info(
                    f"Removed {', '.join(unset_fields.keys())} for Actor record: {actor_id} in {db_ref.name}"
                )

    # ----------------------------------------------------------------------------------------------------
    # * Sync
    # ----------------------------------------------------------------------------------------------------
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(
        description="Synchronize commands", extras={"category": "Console"}
    )
    async def sync(self, interaction: Interaction, global_sync: bool = True):
        await interaction.response.defer(ephemeral=True)
        count = await self.bot.sync_commands(None if global_sync else interaction.guild)
        await interaction.followup.send(
            embed=EmbedX.success(
                title="Commands Synchronization",
                description=f"{count[0]}/{count[1]} command(s) synchronized{" globally" if global_sync else f" to guild: {interaction.guild}"}.",
            ),
            ephemeral=True,
        )

    # ----------------------------------------------------------------------------------------------------
    # * Sync Actors
    # ----------------------------------------------------------------------------------------------------
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(
        description="Update actors with fresh data from associated guild members",
        extras={"category": "Console"},
    )
    async def sync_actors(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            return
        db = self.bot.get_db(guild)
        actors = list(db.find(Actor))
        removed_members_count = 0
        for actor in actors:
            member = None
            try:
                member = guild.get_member(actor.id) or await guild.fetch_member(
                    actor.id
                )
            except:
                pass
            if member:
                actor.is_member = True
                actor.name = member.name
                actor.display_name = member.display_name
            else:
                actor.is_member = False
                removed_members_count += 1
        db.save_all(actors)
        await interaction.followup.send(
            embed=EmbedX.success(
                title="Actors Synchronization",
                description=f"{len(actors)} actor(s) synchronized.\n{removed_members_count} actor(s) no longer members.",
            ),
            ephemeral=True,
        )

    # ----------------------------------------------------------------------------------------------------
    # * Join
    # ----------------------------------------------------------------------------------------------------
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(
        description="Connect, disconnect, or switch voice channel",
        extras={"category": "Console"},
    )
    async def join(self, interaction: Interaction, channel: VoiceChannel | None = None):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            return

        voice_client = guild.voice_client

        try:
            if channel:
                if voice_client:
                    if voice_client.channel.id == channel.id:
                        return await interaction.followup.send(
                            embed=EmbedX.info(f"Already in {channel.mention}."),
                            ephemeral=True,
                        )
                    await voice_client.move_to(channel)
                    await interaction.followup.send(
                        embed=EmbedX.info(f"Switched to {channel.mention}."),
                        ephemeral=True,
                    )
                else:
                    await channel.connect()
                    await interaction.followup.send(
                        embed=EmbedX.info(f"Joined {channel.mention}."), ephemeral=True
                    )
            else:
                if voice_client:
                    await voice_client.disconnect()
                    await interaction.followup.send(
                        embed=EmbedX.info("Disconnected from voice channel."),
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        embed=EmbedX.warning(
                            "Not in a voice channel. Provide a channel to join."
                        ),
                        ephemeral=True,
                    ) 
        except Exception as e:
            await interaction.followup.send( 
                embed=EmbedX.error(f"An error occurred: {e}"),
                ephemeral=True,
            ) 

    # ----------------------------------------------------------------------------------------------------
    # * Proxy
    # ----------------------------------------------------------------------------------------------------
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(
        description="Send a message on your behalf", extras={"category": "Console"}
    )
    @app_commands.describe(
        attachment="File to send along with text",
    )
    async def proxy(
        self,
        interaction: Interaction,
        attachment: Attachment | None = None,
    ):
        await interaction.response.send_modal(TextParagraphModal(attachment=attachment))

    # ----------------------------------------------------------------------------------------------------
    # * Purge
    # ----------------------------------------------------------------------------------------------------
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(
        description="Purge messages in current channel with advanced filters", extras={"category": "Console"}
    )
    @app_commands.describe(
        limit="Number of targeted messages to delete (default: 1)",
        member="Only purge messages from this member (optional)",
        role="Only purge messages from members with this role (optional)",
        message_id="Target a single specific message ID to delete (optional)",
        before="Purge messages before this message ID (optional)",
        after="Purge messages after this message ID (optional)",
    )
    async def purge(
        self,
        interaction: Interaction,
        limit: int = 1,
        member: Member | User | None = None,
        role: Role | None = None,
        message_id: str | None = None,
        before: str | None = None,
        after: str | None = None,
    ):
        """
        Purge messages in the current channel with dynamic criteria scanning.
        """
        try:
            await interaction.response.defer(ephemeral=True)
            channel = interaction.channel
            if not channel:
                return await interaction.followup.send(
                    embed=EmbedX.error("No channel to purge messages from."),
                    ephemeral=True,
                )
            if not isinstance(channel, (TextChannel, Thread, VoiceChannel)):
                return await interaction.followup.send(
                    embed=EmbedX.error(
                        "This command can only be used in text channels, threads, or voice channel chats."
                    ),
                    ephemeral=True,
                )

            # Convert before, after, and target message IDs to integers/objects if provided
            before_msg = None
            after_msg = None
            target_msg_id = None
            
            try:
                if before:
                    before_msg = await channel.fetch_message(int(before))
                if after:
                    after_msg = await channel.fetch_message(int(after))
                if message_id:
                    target_msg_id = int(message_id)
            except Exception as e:
                return await interaction.followup.send(
                    embed=EmbedX.error(f"Invalid message ID provided: {e}"),
                    ephemeral=True,
                )

            # Keep track of how many matching messages we've actually approved for deletion
            deleted_count = 0

            def purge_check(msg: Message) -> bool:
                nonlocal deleted_count
                
                # If we already approved the requested number of deletions, stop matching
                if deleted_count >= limit:
                    return False

                # Filter by exact Message ID
                if target_msg_id and msg.id != target_msg_id:
                    return False

                # Filter by Member
                if member and msg.author.id != member.id:
                    return False

                # Filter by Role (Checks if author is a Member and has the role)
                if role:
                    if not isinstance(msg.author, Member) or role not in msg.author.roles:
                        return False

                # If it passed all filters, it's a match! Increment our target counter.
                deleted_count += 1
                return True

            # Set an upper search ceiling. If looking for a specific message ID, 
            # or a rare user, we might need to sweep through hundreds of records.
            # 1000 is usually a safe maximum history sweep boundary per command call.
            search_history_limit = 1000 if (member or role or target_msg_id) else limit

            # Run the purge with our flexible criteria checklist
            deleted = await channel.purge(
                limit=search_history_limit,
                check=purge_check,
                before=before_msg,
                after=after_msg,
            )

            # Construct a dynamic success message based on used parameters
            filter_details = []
            if member: filter_details.append(f"by {member.mention}")
            if role: filter_details.append(f"from roles matching {role.name}")
            if target_msg_id: filter_details.append(f"with ID `{target_msg_id}`")
            
            suffix = f" matching {" and ".join(filter_details)}" if filter_details else ""

            await interaction.followup.send(
                embed=EmbedX.success(
                    f"Successfully purged {len(deleted)} message(s){suffix}."
                ),
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                embed=EmbedX.error(f"Failed to purge messages: {e}"), ephemeral=True
            )

    #----------------------------------------------------------------------------------------------------
    # * Ban
    #----------------------------------------------------------------------------------------------------
    @app_commands.command(name="ban", description="Bans a member from the server.")
    @app_commands.checks.has_permissions(administrator=True)
    async def ban(self, interaction: Interaction, member: Member, reason: str = "No reason provided"):
        """Standard ban command."""
        try:
            await member.ban(reason=reason)
            await interaction.response.send_message(f"🔨 **{member}** has been banned.", ephemeral=True)
        except Forbidden:
            await interaction.response.send_message("❌ I do not have permission to ban this member. (Check role hierarchy)", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ An error occurred: {e}", ephemeral=True)

    #----------------------------------------------------------------------------------------------------
    # * Unban
    #----------------------------------------------------------------------------------------------------
    @app_commands.command(name="unban", description="Unbans a user cleanly via their User ID.")
    @app_commands.checks.has_permissions(administrator=True)
    async def unban(self, list_interaction: Interaction, user_id: str):
        """
        We use discord.Object(id) so the bot doesn't require the user to be in the server.
        """
        try:
            target_id = int(user_id)
            user_obj = Object(id=target_id)
            await list_interaction.guild.unban(user_obj)
            await list_interaction.response.send_message(f"✅ User ID `{user_id}` has been silently unbanned.", ephemeral=True)
            
        except ValueError:
            await list_interaction.response.send_message("❌ Please provide a valid numerical User ID.", ephemeral=True)
        except NotFound:
            await list_interaction.response.send_message("❌ That user is not currently banned on this server.", ephemeral=True)
        except Forbidden:
            await list_interaction.response.send_message("❌ I lack the 'Ban Members' permission to do that.", ephemeral=True)

    # Error handling for missing permissions
    @ban.error
    @unban.error
    async def mod_error(self, interaction: Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.errors.MissingPermissions):
            await interaction.response.send_message("⛔ You don't have permission to use this command.", ephemeral=True)

    #----------------------------------------------------------------------------------------------------
    # * Role
    #----------------------------------------------------------------------------------------------------
    @app_commands.command(name="role", description="Gives a role to a user.")
    @app_commands.checks.has_permissions(administrator=True)
    async def role(self, interaction: Interaction, member: Member, role: Role):
        try:
            # Check if the role you are trying to give is higher than the bot's highest role
            if role.position >= interaction.guild.me.top_role.position:
                await interaction.response.send_message(
                    "❌ I cannot assign this role. Move my bot role higher in the server settings hierarchy!", 
                    ephemeral=True
                )
                return

            await member.add_roles(role)
            await interaction.response.send_message(
                f"✅ Successfully gave the role **{role.name}** to **{member.display_name}**.", 
                ephemeral=True
            )

        except Forbidden:
            await interaction.response.send_message("❌ I lack the 'Manage Roles' permission on this server.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ An error occurred: {e}", ephemeral=True)

    # Attach the permission error handler for the role command
    @role.error
    async def role_error(self, interaction: Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.errors.MissingPermissions):
            await interaction.response.send_message("⛔ You need 'Manage Roles' permissions to use this command.", ephemeral=True)

    
    #----------------------------------------------------------------------------------------------------
    # * Server
    #----------------------------------------------------------------------------------------------------
    @app_commands.command(
        name="server", 
        description="View or update server configuration settings."
    )
    @app_commands.describe(
        name="Change the server's name",
        icon="Upload a new server icon (Image file)",
        description="Change the server's description (for public/discoverable servers)",
        private_profile="Set if the server profile is private (True/False)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def server_cmd(
        self, 
        interaction: Interaction, 
        name: str|None = None,
        icon: Attachment|None = None,
        description: str|None = None,
        private_profile: bool|None = None
    ):
        guild = interaction.guild
        
        # Guard clause if used outside a server (DM)
        if not guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        # 1. IF NO ARGUMENTS PASSED: Return current server info
        if name is None and icon is None and description is None and private_profile is None:
            embed = EmbedX.info(
                title=f"Server Info", 
            )
            if guild.icon:
                embed.set_thumbnail(url=guild.icon.url)
                
            embed.add_field(name="ID", value=guild.id, inline=True)
            embed.add_field(name="Name", value=guild.name, inline=False)
            embed.add_field(name="Owner", value=guild.owner, inline=True)
            embed.add_field(name="Members", value=guild.member_count, inline=True)
            embed.add_field(name="Description", value=guild.description or "(No description)", inline=False)
            
            # Discord doesn't natively have a "private profile" boolean for guilds, 
            # but we can check features like 'DISCOVERABLE' or display custom state.
            is_discoverable = "DISCOVERABLE" in guild.features
            embed.add_field(name="Publicly Discoverable?", value="Yes" if is_discoverable else "No", inline=True)

            await interaction.response.send_message(embed=embed)
            return

        # 2. IF ARGUMENTS PASSED: Update the server settings
        await interaction.response.defer(ephemeral=True) # Defer since API calls take time
        changes = []

        try:
            # Update Name
            if name is not None:
                await guild.edit(name=name)
                changes.append(f"• **Name** updated to: `{name}`")

            # Update Description
            if description is not None:
                await guild.edit(description=description)
                changes.append(f"• **Description** updated to: `{description}`")

            # Update Icon
            if icon is not None:
                if icon.content_type and icon.content_type.startswith("image/"):
                    icon_bytes = await icon.read()
                    await guild.edit(icon=icon_bytes)
                    changes.append("• **Server Icon** updated successfully.")
                else:
                    await interaction.followup.send("❌ Provided file for the icon must be an image.", ephemeral=True)
                    return

            # Update Private Profile 
            # Note: Native discord servers don't have a "private_profile" toggle. 
            # This toggle changes community discovery features if the server qualifies.
            if private_profile is not None:
                if private_profile:
                    if "DISCOVERABLE" in guild.features:
                        await guild.remove_features("DISCOVERABLE")
                    changes.append("• **Private Profile**: Enabled (Removed from public discovery if applicable).")
                else:
                    # Enabling discoverability usually requires meeting explicit Discord requirements,
                    # but we attempt to add it here.
                    try:
                        await guild.add_features("DISCOVERABLE")
                        changes.append("• **Private Profile**: Disabled (Added to public discovery).")
                    except HTTPException:
                        changes.append("• **Private Profile**: Failed to disable. (Server may not meet Discord's community discovery requirements).")

            # Send success confirmation
            result_embed = EmbedX.success(
                title="Server Settings Updated",
                description="\n".join(changes),
            )
            await interaction.followup.send(embed=result_embed, ephemeral=True)

        except Forbidden:
            await interaction.followup.send("❌ I do not have the required permissions (`Manage Server`) to change these settings.", ephemeral=True)
        except HTTPException as e:
            await interaction.followup.send(f"❌ An error occurred while updating settings: {e}", ephemeral=True)