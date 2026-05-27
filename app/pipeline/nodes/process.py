import logging
from app.pipeline.state import PipelineState
from app.services.process_anthropic import process_anthropic_markdown
from app.services.process_youtube import process_youtube_transcripts

logger = logging.getLogger(__name__)


def process_node(state: PipelineState) -> dict:
    try:
        limit = state.get("limit")
        anthropic_result = process_anthropic_markdown(limit=limit)
        youtube_result = process_youtube_transcripts(limit=limit)
        return {
            "anthropic_processed": anthropic_result["processed"],
            "anthropic_failed": anthropic_result["failed"],
            "youtube_processed": youtube_result["processed"],
            "youtube_unavailable": youtube_result.get("unavailable", 0),
        }
    except Exception as e:
        logger.error(f"Process node failed: {e}", exc_info=True)
        return {
            "anthropic_processed": 0,
            "anthropic_failed": 0,
            "youtube_processed": 0,
            "youtube_unavailable": 0,
            "errors": state.get("errors", []) + [f"process: {e}"],
        }
