from langgraph.graph import StateGraph, START, END
from app.pipeline.state import PipelineState
from app.pipeline.nodes.scrape import scrape_node
from app.pipeline.nodes.process import process_node
from app.pipeline.nodes.digest import digest_node
from app.pipeline.nodes.email import email_node
from app.pipeline.nodes.finalize import finalize_node


def build_pipeline_graph():
    builder = StateGraph(PipelineState)

    builder.add_node("scrape", scrape_node)
    builder.add_node("process", process_node)
    builder.add_node("digest", digest_node)
    builder.add_node("send_email", email_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "scrape")
    builder.add_edge("scrape", "process")
    builder.add_edge("process", "digest")
    builder.add_edge("digest", "send_email")
    builder.add_edge("send_email", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile()


pipeline_graph = build_pipeline_graph()
