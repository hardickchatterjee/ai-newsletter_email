from app.pipeline.state import PipelineState


def finalize_node(state: PipelineState) -> dict:
    success = (
        state.get("email_error_count", 0) == 0
        and (
            state.get("email_success_count", 0) + state.get("email_skip_count", 0) > 0
        )
    )
    return {"success": success}
