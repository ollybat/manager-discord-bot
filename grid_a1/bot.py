
@bot.tree.command(name="verifypanel", description="Create a verification panel")
@app_commands.checks.has_permissions(manage_guild=True)
async def verifypanel(i: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    if role.is_default(): return await i.response.send_message("❌ You cannot use @everyone as the verification role.", ephemeral=True)
    if not i.guild.me or role >= i.guild.me.top_role: return await i.response.send_message("❌ Move Grid A1's bot role above the verification role first.", ephemeral=True)
    panel = embed("💜 Grid A1 • Secure Verification", "✨ Complete the short verification check to unlock the server.\n\n📜 Rules confirmation\n✅ Verified role for eligible members\n\nDiscord-wide moderation history is private and unavailable to bots.", discord.Colour.from_rgb(177, 77, 255))
    panel.add_field(name="🔐 Verification steps", value="1️⃣ Start verification\n2️⃣ Review your result\n3️⃣ Confirm the rules\n4️⃣ Receive access", inline=False)
    panel.add_field(name="💬 Need help?", value="If you need help, please open a support ticket.", inline=False)
    panel.set_footer(text="Grid A1 • Secure, fair, Discord-only verification")
    message = await channel.send(embed=panel, view=VerifyPanel(bot.database))
    bot.database.upsert_config(i.guild.id, verify_panel_channel=channel.id, verify_panel_message=message.id, verify_role=role.id)
    await i.response.send_message(f"✅ Verification panel created in {channel.mention} for {role.mention}.", ephemeral=True)


@setup_group.command(name="staff", description="Manage optional ticket staff notification roles")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(action="Choose whether to add or remove this staff role", role="Staff role to notify")
@app_commands.choices(action=[app_commands.Choice(name="Add staff notifications", value="add"), app_commands.Choice(name="Remove staff notifications", value="remove")])
async def setup_staff(i: discord.Interaction, action: app_commands.Choice[str], role: discord.Role):
    if role.is_default() or role.is_managed(): return await i.response.send_message("❌ Choose a normal staff role, not @everyone or an integration role.", ephemeral=True)
    action = action.value.lower()
    if action not in ("add", "remove"):
        return await i.response.send_message("Use action add or remove.", ephemeral=True)
    try:
        changed = bot.database.add_staff_role(i.guild.id, role.id) if action == "add" else bot.database.remove_staff_role(i.guild.id, role.id)
    except ValueError as error:
        return await i.response.send_message(f"❌ {error}", ephemeral=True)
    if action == "add":
        message = f"✅ {role.mention} will be notified when a new ticket is created." if changed else f"{role.mention} is already configured."

[55 more lines in file. Use offset=130 to continue.]