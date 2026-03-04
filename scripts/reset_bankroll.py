"""Reset current bankroll setting to a fixed baseline.

Usage:
    python -m scripts.reset_bankroll
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from api.database import apply_runtime_migrations, async_session_factory
from api.models import Setting

logger = logging.getLogger(__name__)


async def run() -> None:
    await apply_runtime_migrations()

    async with async_session_factory() as db_session:
        result = await db_session.execute(
            select(Setting).where(Setting.key == "current_bankroll")
        )
        setting = result.scalar_one_or_none()

        previous_value = setting.value if setting else None
        if setting:
            setting.value = "1000.00"
            setting.updated_at = datetime.now(timezone.utc)
        else:
            db_session.add(
                Setting(
                    key="current_bankroll",
                    value="1000.00",
                    updated_at=datetime.now(timezone.utc),
                )
            )

        await db_session.commit()

    logger.info(
        "Bankroll reset complete: previous=%s new=1000.00",
        previous_value if previous_value is not None else "<unset>",
    )
    print(
        f"Bankroll reset complete: previous={previous_value if previous_value is not None else '<unset>'} new=1000.00"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
