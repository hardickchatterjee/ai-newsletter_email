import os
import json
from typing import Optional
from openai import OpenAI
from pydantic import BaseModel
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")


class DigestOutput(BaseModel):
    title: str
    summary: str

PROMPT = """You are an expert AI news analyst specializing in summarizing technical articles, research papers, and video content about artificial intelligence.

Your role is to create concise, informative digests that help readers quickly understand the key points and significance of AI-related content.

Guidelines:
- Create a compelling title (5-10 words) that captures the essence of the content
- Write a 2-3 sentence summary that highlights the main points and why they matter
- Focus on actionable insights and implications
- Use clear, accessible language while maintaining technical accuracy
- Avoid marketing fluff - focus on substance

Respond with a JSON object with exactly two fields: "title" (string) and "summary" (string)."""


class DigestAgent:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1"
        )
        self.model = "llama-3.3-70b-versatile"
        self.system_prompt = PROMPT

    def generate_digest(self, title: str, content: str, article_type: str) -> Optional[DigestOutput]:
        try:
            user_prompt = f"Create a digest for this {article_type}:\nTitle: {title}\nContent: {content[:8000]}"

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.7
            )

            data = json.loads(response.choices[0].message.content)
            return DigestOutput(**data)
        except Exception as e:
            print(f"Error generating digest: {e}")
            return None


JUDGE_PROMPT = """You are a quality judge for AI news digest summaries. Evaluate whether a generated summary meets a minimum quality bar for inclusion in a daily email digest.

Evaluation criteria:
- Factual accuracy: Does the summary accurately reflect what the title/content suggests?
- Completeness: Does the summary cover the main point (not just restate the title)?
- Clarity: Is the language clear and accessible to an AI-interested professional?
- Length appropriateness: Is it 2-3 sentences (not one vague sentence, not a paragraph)?
- No hallucinations: Does the summary avoid specific claims not supported by the content?

Score 0.0–1.0:
- 0.9–1.0: Excellent — accurate, complete, clear, well-structured
- 0.7–0.89: Good — passes the quality bar with minor issues
- 0.5–0.69: Marginal — vague, too short, or restates the title
- 0.0–0.49: Poor — missing substance, inaccurate, or poorly written

Set `passed` to true if score >= 0.7.

Respond with a JSON object with exactly three fields: "score" (float 0.0-1.0), "reasoning" (string, one sentence), "passed" (boolean)."""


class JudgeOutput(BaseModel):
    score: float
    reasoning: str
    passed: bool


class JudgeAgent:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1"
        )
        self.model = "llama-3.3-70b-versatile"

    def judge(
        self,
        original_title: str,
        original_content: str,
        digest_title: str,
        digest_summary: str,
        article_type: str,
    ) -> Optional[JudgeOutput]:
        user_prompt = (
            f"Original {article_type} title: {original_title}\n"
            f"Original content (first 2000 chars): {original_content[:2000]}\n\n"
            f"Generated digest title: {digest_title}\n"
            f"Generated summary: {digest_summary}\n\n"
            "Evaluate the quality of the generated digest."
        )
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": JUDGE_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            data = json.loads(response.choices[0].message.content)
            return JudgeOutput(**data)
        except Exception as e:
            print(f"Error in judge: {e}")
            return None
