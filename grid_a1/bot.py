        try:
            out=[]
            if settings.test_guild_id:
                guild=discord.Object(id=settings.test_guild_id); self.tree.copy_global_to(guild=guild); synced_guild=await self.tree.sync(guild=guild); out.append(f"test guild `{settings.test_guild_id}`: {len(synced_guild)}")
            synced_global=await self.tree.sync(); out.append(f"global: {len(synced_global)}"); return out
        finally: self._global_sync_in_progress = False
    @tasks.loop(seconds=60)
    async def refresh_panels(self):
        for guild in self.guilds:
            config = self.database.config(guild.id)
            if not config or not config['panel_channel'] or not config['panel_message']: continue
            channel = guild.get_channel(config['panel_channel'])
            if not isinstance(channel, discord.TextChannel): continue
            try:
                message = await channel.fetch_message(config['panel_message']); await message.edit(embed=support_panel(guild, self.database), view=TicketPanel(self.tickets))
            except discord.NotFound:
                try:
                    message = await channel.send(embed=support_panel(guild, self.database), view=TicketPanel(self.tickets)); self.database.upsert_config(guild.id, panel_message=message.id)
                except discord.DiscordException: log.exception("Panel recovery failed")
            except discord.DiscordServerError as error:
                log.warning("Panel refresh temporarily unavailable (HTTP %s); will retry next cycle", getattr(error, "status", "unknown"))
            except discord.HTTPException as error:
                log.warning("Panel refresh request failed (HTTP %s); will retry next cycle", getattr(error, "status", "unknown"))
            except discord.DiscordException:
                log.warning("Panel refresh encountered a Discord error; will retry next cycle")
    @tasks.loop(minutes=5)
    async def inactivity_loop(self):
        from datetime import datetime, timezone, timedelta
        for guild in self.guilds:
            config = self.database.config(guild.id)
            if not config: continue
            threshold = int(config['inactivity_hours'])
            for row in self.database.open_tickets(guild.id):
                channel = guild.get_channel(row['channel_id'])
                if not isinstance(channel, discord.TextChannel): continue
                indicator, duration = inactivity_indicator(row['last_activity_at'], threshold, bool(row['owner_left']))
                red = indicator == '🔴'
                if red and not row['inactivity_notice_at']:
                    owner = guild.get_member(row['owner_id'])
                    now = datetime.now(timezone.utc); notice_at = now.isoformat()

[236 more lines in file. Use offset=80 to continue.]