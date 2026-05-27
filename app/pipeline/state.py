from typing import TypedDict, List, Dict, Any, Optional


class PipelineState(TypedDict):
    hours: int
    top_n: int
    limit: Optional[int]  # cap items per stage (process/digest); None = no limit
    scrape_youtube_count: int
    scrape_openai_count: int
    scrape_anthropic_count: int
    anthropic_processed: int
    anthropic_failed: int
    youtube_processed: int
    youtube_unavailable: int
    digest_total: int
    digest_processed: int
    digest_failed: int
    email_results: List[Dict[str, Any]]
    email_success_count: int
    email_skip_count: int
    email_error_count: int
    errors: List[str]
    success: bool
