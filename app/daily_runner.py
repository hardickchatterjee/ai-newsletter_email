import logging
import argparse
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

from app.database.create_tables import create_tables
from app.pipeline.state import PipelineState
from app.pipeline.workflow import pipeline_graph

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def run_daily_pipeline(hours: int = 24, top_n: int = 10, limit: Optional[int] = None) -> dict:
    create_tables()
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("Starting Daily AI News Aggregator Pipeline (LangGraph)")
    if limit:
        logger.info(f"DEV MODE: limit={limit} items per stage, hours={hours}")
    logger.info("=" * 60)

    initial_state: PipelineState = {
        "hours": hours,
        "top_n": top_n,
        "limit": limit,
        "scrape_youtube_count": 0,
        "scrape_openai_count": 0,
        "scrape_anthropic_count": 0,
        "anthropic_processed": 0,
        "anthropic_failed": 0,
        "youtube_processed": 0,
        "youtube_unavailable": 0,
        "digest_total": 0,
        "digest_processed": 0,
        "digest_failed": 0,
        "email_results": [],
        "email_success_count": 0,
        "email_skip_count": 0,
        "email_error_count": 0,
        "errors": [],
        "success": False,
    }

    try:
        final_state = pipeline_graph.invoke(initial_state)
    except Exception as e:
        logger.error(f"Pipeline graph failed: {e}", exc_info=True)
        final_state = {**initial_state, "success": False, "errors": [str(e)]}

    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()

    logger.info("\n" + "=" * 60)
    logger.info("Pipeline Summary")
    logger.info("=" * 60)
    logger.info(f"Duration: {duration:.1f}s")
    logger.info(
        f"Scraped: YT={final_state['scrape_youtube_count']} "
        f"OAI={final_state['scrape_openai_count']} "
        f"Anthropic={final_state['scrape_anthropic_count']}"
    )
    logger.info(
        f"Processing: Anthropic={final_state['anthropic_processed']} "
        f"YouTube={final_state['youtube_processed']}"
    )
    logger.info(
        f"Digests: {final_state['digest_processed']}/{final_state['digest_total']} "
        f"({final_state['digest_failed']} failed)"
    )
    logger.info(
        f"Emails: sent={final_state['email_success_count']} "
        f"skipped={final_state['email_skip_count']} "
        f"errors={final_state['email_error_count']}"
    )
    if final_state.get("errors"):
        logger.warning(f"Pipeline errors: {final_state['errors']}")
    for r in final_state.get("email_results", []):
        if not r.get("success") and not r.get("skipped"):
            logger.error(f"Email error [{r.get('user')}]: {r.get('error')}")
    logger.info("=" * 60)

    return {
        **final_state,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "duration_seconds": duration,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--limit", type=int, default=None, help="Cap items per stage for quick testing")
    args = parser.parse_args()
    result = run_daily_pipeline(hours=args.hours, top_n=args.top_n, limit=args.limit)
    exit(0 if result["success"] else 1)
