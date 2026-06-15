"""Имитация человеческого движения мыши и клика через CDP."""

import logging
import random
import time

logger = logging.getLogger(__name__)


def human_pause(min_sec: float = 0.4, max_sec: float = 1.2) -> None:
    time.sleep(random.uniform(min_sec, max_sec))


def human_like_click_at_viewport(
    driver, x: float, y: float, context: str
) -> bool:
    jitter_x = random.uniform(-4.0, 4.0)
    jitter_y = random.uniform(-3.0, 3.0)
    target_x = x + jitter_x
    target_y = y + jitter_y

    start_x = target_x + random.uniform(-100.0, 100.0)
    start_y = target_y + random.uniform(-70.0, 70.0)
    steps = random.randint(10, 18)

    try:
        for step in range(steps + 1):
            t = step / steps
            ease = t * t * (3.0 - 2.0 * t)
            cx = start_x + (target_x - start_x) * ease
            cy = start_y + (target_y - start_y) * ease
            driver.execute_cdp_cmd(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseMoved",
                    "x": cx,
                    "y": cy,
                    "button": "left",
                },
            )
            time.sleep(random.uniform(0.006, 0.022))

        human_pause(0.04, 0.14)

        driver.execute_cdp_cmd(
            "Input.dispatchMouseEvent",
            {
                "type": "mousePressed",
                "x": target_x,
                "y": target_y,
                "button": "left",
                "clickCount": 1,
            },
        )
        time.sleep(random.uniform(0.06, 0.14))
        driver.execute_cdp_cmd(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseReleased",
                "x": target_x,
                "y": target_y,
                "button": "left",
                "clickCount": 1,
            },
        )
        logger.info(
            "ЛКМ human-like (%s) at (%.0f, %.0f)", context, target_x, target_y
        )
        return True
    except Exception as exc:
        logger.debug("Human-like click failed (%s): %s", context, exc)
        return False
