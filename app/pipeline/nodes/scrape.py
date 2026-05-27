import logging
from app.pipeline.state import PipelineState
from app.runner import run_scrapers
from app.database.repository import Repository

logger = logging.getLogger(__name__)


def scrape_node(state: PipelineState) -> dict:
    try:
        repo = Repository()
        channel_ids = repo.get_all_active_channel_ids() or None
        results = run_scrapers(hours=state["hours"], channel_ids=channel_ids)
        return {
            "scrape_youtube_count": len(results.get("youtube", [])),
            "scrape_openai_count": len(results.get("openai", [])),
            "scrape_anthropic_count": len(results.get("anthropic", [])),
        }
    except Exception as e:
        logger.error(f"Scrape node failed: {e}", exc_info=True)
        return {
            "scrape_youtube_count": 0,
            "scrape_openai_count": 0,
            "scrape_anthropic_count": 0,
            "errors": state.get("errors", []) + [f"scrape: {e}"],
        }
