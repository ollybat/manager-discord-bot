"""Server logos: the bundled SpinBack mark, a per-server upload, and the helpers that post them.

A server that uploads its own picture with /setup logo sees that picture on every panel and
message; every other server keeps the bundled SpinBack mark. The bytes live in the database,
so a restart, a redeploy, or a move to another host changes nothing.

The bundled mark comes in two sizes: the full picture for banners, and a compact 96x96 copy for
headings, so a panel's title reads first and the picture supports it.
"""
from __future__ import annotations

import io
import logging
from typing import Any, Optional

import discord

from .brand_logo import (
    LOGO_CONTENT_TYPE,
    LOGO_FILENAME,
    LOGO_HEIGHT,
    LOGO_WIDTH,
    MARK_CONTENT_TYPE,
    MARK_FILENAME,
    bundled_logo_bytes,
    bundled_mark_bytes,
)

log = logging.getLogger("grid-a1-logo")

MAX_LOGO_BYTES = 512 * 1024
MAX_LOGO_KB = MAX_LOGO_BYTES // 1024
EXTENSIONS = {"png": "png", "jpeg": "jpg", "gif": "gif", "webp": "webp"}
UPLOAD_FILENAME = "spinback-logo.{extension}"
SUPPORTED_TEXT = "PNG, JPEG, GIF, or WebP"


class LogoRejected(ValueError):
    """Raised with a member-facing reason when an upload cannot be used as a logo."""


def sniff_image(data: bytes) -> Optional[str]:
    """Identify a supported image from its own header bytes, never from the filename."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def validate_upload(data: bytes) -> tuple[bytes, str, str]:
    """Check an uploaded picture and return its bytes, the filename to attach, and its type."""
    if not data:
        raise LogoRejected(f"That file is empty. Attach a {SUPPORTED_TEXT} picture.")
    if len(data) > MAX_LOGO_BYTES:
        raise LogoRejected(
            f"That picture is {len(data) // 1024} KB. Keep logos under {MAX_LOGO_KB} KB so every panel stays quick to load."
        )
    kind = sniff_image(data)
    if kind is None:
        raise LogoRejected(f"That is not a {SUPPORTED_TEXT} image, so Discord would not show it.")
    return bytes(data), UPLOAD_FILENAME.format(extension=EXTENSIONS[kind]), f"image/{kind}"


def bundled_logo() -> tuple[bytes, str, str]:
    """The mark every server starts with."""
    return bundled_logo_bytes(), LOGO_FILENAME, LOGO_CONTENT_TYPE


def stored_logo(guild_id: Any, database: Any = None) -> Optional[tuple[bytes, str, str]]:
    """One server's uploaded picture, or None when it still uses the bundled mark."""
    if not guild_id or database is None:
        return None
    reader = getattr(database, "guild_logo", None)
    if not callable(reader):
        return None
    try:
        row = reader(int(guild_id))
    except Exception:
        log.exception("Could not read the stored logo for guild %s; using the bundled mark", guild_id)
        return None
    if row is None:
        return None
    try:
        data = bytes(row["logo"])
        filename = str(row["filename"])
        content_type = str(row["content_type"])
    except (IndexError, KeyError, TypeError):
        log.warning("The stored logo row for guild %s is unreadable; using the bundled mark", guild_id)
        return None
    if not data or sniff_image(data) is None:
        log.warning("The stored logo for guild %s is not a usable picture; using the bundled mark", guild_id)
        return None
    return data, filename, content_type


def guild_logo(guild_id: Any = 0, database: Any = None) -> tuple[bytes, str, str]:
    """The picture one server should see: its own upload when it has one, else the bundled mark."""
    return stored_logo(guild_id, database) or bundled_logo()


def bundled_mark() -> tuple[bytes, str, str]:
    """The compact mark every server starts with, for headings."""
    return bundled_mark_bytes(), MARK_FILENAME, MARK_CONTENT_TYPE


def guild_mark(guild_id: Any = 0, database: Any = None) -> tuple[bytes, str, str]:
    """The picture beside a heading: this server's own upload when it has one, else the compact mark."""
    return stored_logo(guild_id, database) or bundled_mark()


def guild_mark_url(guild_id: Any = 0, database: Any = None) -> str:
    """The attachment URL for one guild's heading mark."""
    _data, filename, _content_type = guild_mark(guild_id, database)
    return logo_url(filename)


def mark_file(guild_id: Any = 0, database: Any = None) -> discord.File:
    """The file to attach whenever a message shows the compact mark."""
    data, filename, _content_type = guild_mark(guild_id, database)
    return discord.File(io.BytesIO(data), filename=filename)


def logo_url(filename: str) -> str:
    """The attachment URL that an embed or a media item points at."""
    return f"attachment://{filename}"


def guild_logo_url(guild_id: Any = 0, database: Any = None) -> str:
    """The attachment URL for one guild, without building the file twice."""
    _data, filename, _content_type = guild_logo(guild_id, database)
    return logo_url(filename)


def logo_file(guild_id: Any = 0, database: Any = None) -> discord.File:
    """The file to attach whenever a message shows the logo."""
    data, filename, _content_type = guild_logo(guild_id, database)
    return discord.File(io.BytesIO(data), filename=filename)


def attach_logo(
    embed: discord.Embed,
    guild_id: Any = 0,
    database: Any = None,
    *,
    thumbnail: bool = False,
) -> discord.File:
    """Point an embed at this server's logo and return the file to send beside it."""
    data, filename, _content_type = guild_logo(guild_id, database)
    url = logo_url(filename)
    if thumbnail:
        embed.set_thumbnail(url=url)
    else:
        embed.set_image(url=url)
    return discord.File(io.BytesIO(data), filename=filename)


def source_label(guild_id: Any, database: Any = None) -> str:
    """How the setup status page describes this server's logo."""
    if stored_logo(guild_id, database):
        return "Your uploaded logo"
    return f"The bundled {LOGO_WIDTH}x{LOGO_HEIGHT} SpinBack mark"
