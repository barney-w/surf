#!/usr/bin/env python3
"""
Start surf-lab development environment in iTerm2.

Tab 1 (teal) - Services:
  Left:   API server (just dev)
  Middle: Web frontend (just web)
  Right:  Free terminal (surf-lab)

Tab 2 (green) - Claude Code:
  3 vertical columns of ccv instances

Tab 3 (green) - Claude Code 2:
  3 vertical columns of ccv instances (surf-lab)

Tab 4 (orange) - Surf-Kit:
  Left:  pnpm run dev
  Right: ccv instance

Run with: python3 scripts/start_dev.py
"""
import os
import subprocess
import sys
from pathlib import Path

VENV_DIR = Path(__file__).resolve().parent / ".venv"


def ensure_venv():
    """Create a venv with iterm2 installed, then re-exec inside it."""
    python = VENV_DIR / "bin" / "python3"

    if python.exists():
        # Already in the venv — nothing to do
        if sys.prefix == str(VENV_DIR):
            return
        # Re-exec inside the venv
        os.execv(str(python), [str(python), *sys.argv])

    print("Creating venv and installing iterm2 …")
    subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])
    subprocess.check_call([str(python), "-m", "pip", "install", "-q", "iterm2"])
    os.execv(str(python), [str(python), *sys.argv])


ensure_venv()

import iterm2  # noqa: E402
import asyncio  # noqa: E402

PROJECT_ROOT = "/Volumes/ExternalHDD/GitHub/surf-lab"

# Tab colours
TEAL = iterm2.Color(0, 180, 180)
GREEN = iterm2.Color(80, 180, 80)
ORANGE = iterm2.Color(220, 150, 50)

SURF_KIT_ROOT = "/Volumes/ExternalHDD/GitHub/surf-kit"

CCV_CMD = (
    "env"
    " ENABLE_BACKGROUND_TASKS=true"
    " FORCE_AUTO_BACKGROUND_TASKS=true"
    " CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=true"
    " CLAUDE_CODE_ENABLE_UNIFIED_READ_TOOL=true"
    " claude --dangerously-skip-permissions\n"
)


async def style_tab(tab, colour, title):
    """Set tab title and colour."""
    await tab.async_set_title(title)
    for session in tab.sessions:
        profile = await session.async_get_profile()
        await profile.async_set_use_tab_color(True)
        await profile.async_set_tab_color(colour)


async def set_badge(session, text, small=False):
    """Set a pane badge (watermark) on a session."""
    profile = await session.async_get_profile()
    await profile.async_set_badge_text(text)
    if small:
        await profile.async_set_badge_max_width(0.25)
        await profile.async_set_badge_max_height(0.1)


async def main(connection):
    app = await iterm2.app.async_get_app(connection)

    window = app.current_terminal_window
    if window is None:
        window = await iterm2.Window.async_create(connection)

    # --- Tab 1: Services (teal) ---
    tab1 = await window.async_create_tab()
    s_left = tab1.current_session
    s_mid = await s_left.async_split_pane(vertical=True)
    s_right = await s_mid.async_split_pane(vertical=True)
    await asyncio.sleep(0.3)

    await style_tab(tab1, TEAL, "SURF: Services")
    await set_badge(s_left, "API", small=True)
    await set_badge(s_mid, "Web", small=True)
    await set_badge(s_right, "Terminal", small=True)

    await s_left.async_send_text(f"cd {PROJECT_ROOT} && just dev\n")
    await s_mid.async_send_text(f"cd {PROJECT_ROOT} && just web\n")
    await s_right.async_send_text(f"cd {PROJECT_ROOT}\n")

    # --- Tab 2: Claude Code (green) — 3 vertical columns ---
    tab2 = await window.async_create_tab()
    col1 = tab2.current_session
    col2 = await col1.async_split_pane(vertical=True)
    col3 = await col2.async_split_pane(vertical=True)
    await asyncio.sleep(0.3)

    await style_tab(tab2, GREEN, "SURF: Claude")

    claude_sessions = [col1, col2, col3]
    for i, session in enumerate(claude_sessions, 1):
        await set_badge(session, f"Claude {i}", small=True)
        await session.async_send_text(f"cd {PROJECT_ROOT}\n")
        await session.async_send_text(CCV_CMD)

    # --- Tab 3: Claude Code 2 (green) — 3 vertical columns ---
    tab3 = await window.async_create_tab()
    c2_col1 = tab3.current_session
    c2_col2 = await c2_col1.async_split_pane(vertical=True)
    c2_col3 = await c2_col2.async_split_pane(vertical=True)
    await asyncio.sleep(0.3)

    await style_tab(tab3, GREEN, "SURF: Claude 2")

    claude2_sessions = [c2_col1, c2_col2, c2_col3]
    for i, session in enumerate(claude2_sessions, 1):
        await set_badge(session, f"Claude {i + 3}", small=True)
        await session.async_send_text(f"cd {PROJECT_ROOT}\n")
        await session.async_send_text(CCV_CMD)

    # --- Tab 4: Surf-Kit (orange) — dev + claude ---
    tab4 = await window.async_create_tab()
    sk_left = tab4.current_session
    sk_right = await sk_left.async_split_pane(vertical=True)
    await asyncio.sleep(0.3)

    await style_tab(tab4, ORANGE, "SURF-KIT")
    await set_badge(sk_left, "Dev", small=True)
    await sk_left.async_send_text(f"cd {SURF_KIT_ROOT} && pnpm run dev\n")
    await set_badge(sk_right, "Claude 7", small=True)
    await sk_right.async_send_text(f"cd {SURF_KIT_ROOT}\n")
    await sk_right.async_send_text(CCV_CMD)

    # Focus services tab
    await tab1.async_select()
    await s_left.async_activate()

    # Close the initial empty tab if it exists
    created_tabs = {tab1, tab2, tab3, tab4}
    tabs = window.tabs
    if len(tabs) > len(created_tabs):
        initial_tab = tabs[0]
        if initial_tab not in created_tabs:
            await initial_tab.sessions[0].async_send_text("exit\n")


iterm2.run_until_complete(main)
