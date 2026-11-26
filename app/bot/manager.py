
import asyncio
import logging

from app.bot.config import load_config
from app.bot.discord_bot import SupportBot


logger = logging.getLogger("posperity.bot.manager")

bot = None
bot_task = None


async def start_bot():
    global bot, bot_task

    config = load_config()

    bot = SupportBot(config)

    bot_task = asyncio.create_task(
        bot.start(config.token)
    )

    try:
        await asyncio.wait_for(
            bot.wait_until_ready(),
            timeout=30
        )

        logger.info("Discord bot connected successfully.")

    except Exception:
        logger.exception("Discord bot failed to start.")

        if bot is not None:
            await bot.close()

        if bot_task is not None:
            try:
                await bot_task
            except Exception:
                pass

        bot = None
        bot_task = None

        raise


async def stop_bot():
    global bot, bot_task

    if bot is not None:
        await bot.close()

    if bot_task is not None:
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Error while stopping Discord bot.")

    bot = None
    bot_task = None

    logger.info("Discord bot stopped.")


def get_bot():
    return bot
