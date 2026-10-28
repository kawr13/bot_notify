from __future__ import annotations

import os

from dotenv import load_dotenv


load_dotenv()


def get_vk_token() -> str | None:
    return (
        os.getenv("VK_GROUP_TOKEN")
        or os.getenv("VK_ACCESS_TOKEN")
        or None
    )
