import logging
from app.pipeline.state import PipelineState
from app.services.process_digest import process_digests

logger = logging.getLogger(__name__)


def digest_node(state: PipelineState) -> dict:
    try:
        result = process_digests(limit=state.get("limit"))
        return {
            "digest_total": result["total"],
            "digest_processed": result["processed"],
            "digest_failed": result["failed"],
        }
    except Exception as e:
        logger.error(f"Digest node failed: {e}", exc_info=True)
        return {
            "digest_total": 0,
            "digest_processed": 0,
            "digest_failed": 0,
            "errors": state.get("errors", []) + [f"digest: {e}"],
        }
