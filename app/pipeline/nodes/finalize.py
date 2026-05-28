from app.pipeline.state import PipelineState


def finalize_node(state: PipelineState) -> dict:
    success = state.get("email_error_count", 0) == 0
    return {"success": success}
