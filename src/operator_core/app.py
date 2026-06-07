from __future__ import annotations

import logging

from operator_core.activation import check_activation_readiness
from operator_core.bootstrap import build_bootstrap_context
from operator_core.logging import configure_logging
from operator_core.runtime import OperatorRuntime


def main() -> int:
    context = build_bootstrap_context()
    configure_logging(context.settings.log_level)

    logger = logging.getLogger("operator_core")
    logger.info(
        "operator core bootstrap ready | project=%s env=%s log_level=%s runtime=%s runtime_mode=%s telegram_enabled=%s airtable_enabled=%s openai_enabled=%s",
        context.settings.active_project,
        context.settings.env,
        context.settings.log_level,
        context.runtime_path,
        context.settings.runtime_mode,
        context.settings.telegram.enabled,
        context.settings.airtable.enabled,
        context.settings.openai.enabled,
    )

    _log_activation_readiness(logger, context.settings)

    runtime = OperatorRuntime(context)
    runtime.run()

    return 0


def _log_activation_readiness(
    logger: logging.Logger,
    settings: "object",
) -> None:
    from operator_core.config import Settings

    if not isinstance(settings, Settings):
        return

    readiness = check_activation_readiness(settings)

    for integration in (readiness.telegram, readiness.airtable, readiness.openai):
        if not integration.enabled:
            logger.debug(
                "activation readiness | integration=%s enabled=false",
                integration.name,
            )
        elif integration.ready:
            logger.info(
                "activation readiness | integration=%s enabled=true ready=true",
                integration.name,
            )
        else:
            logger.warning(
                "activation readiness | integration=%s enabled=true ready=false issues=%s",
                integration.name,
                "; ".join(integration.issues),
            )

    if readiness.fully_ready:
        logger.info(
            "activation readiness | overall=ready project=%s",
            readiness.project_key,
        )
    else:
        logger.warning(
            "activation readiness | overall=not_ready project=%s blocking_issues=%s",
            readiness.project_key,
            "; ".join(readiness.blocking_issues),
        )
