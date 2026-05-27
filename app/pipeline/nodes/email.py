import logging
from app.pipeline.state import PipelineState
from app.services.process_email import send_digest_email_for_user
from app.database.repository import Repository

logger = logging.getLogger(__name__)


def email_node(state: PipelineState) -> dict:
    repo = Repository()
    users = repo.get_all_active_users()

    results = []
    success_count = 0
    skip_count = 0
    error_count = 0

    for user in users:
        try:
            result = send_digest_email_for_user(
                user=user,
                hours=state["hours"],
                top_n=state["top_n"],
            )
            results.append(result)
            if result.get("skipped"):
                skip_count += 1
            elif result.get("success"):
                success_count += 1
            else:
                error_count += 1
                logger.error(
                    f"Email failed for {user.email}: {result.get('error', 'unknown error')}",
                    exc_info=False,
                )
        except Exception as e:
            logger.error(f"Failed to send email for {user.email}: {e}", exc_info=True)
            error_count += 1
            results.append({"success": False, "user": user.email, "error": str(e)})

    return {
        "email_results": results,
        "email_success_count": success_count,
        "email_skip_count": skip_count,
        "email_error_count": error_count,
    }
