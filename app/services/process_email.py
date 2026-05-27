import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from app.agent.email_agent import EmailAgent, RankedArticleDetail, EmailDigestResponse
from app.agent.curator_agent import CuratorAgent
from app.profiles.user_profile import USER_PROFILE
from app.database.models import User
from app.database.repository import Repository
from app.services.email_utils import send_email, digest_to_html

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def generate_email_digest(hours: int = 24, top_n: int = 10) -> EmailDigestResponse:
    curator = CuratorAgent(USER_PROFILE)
    email_agent = EmailAgent(USER_PROFILE)
    repo = Repository()
    
    digests = repo.get_recent_digests(hours=hours)
    total = len(digests)
    
    if total == 0:
        logger.warning(f"No digests found from the last {hours} hours")
        raise ValueError("No digests available")
    
    logger.info(f"Ranking {total} digests for email generation")
    ranked_articles = curator.rank_digests(digests)
    
    if not ranked_articles:
        logger.error("Failed to rank digests")
        raise ValueError("Failed to rank articles")
    
    logger.info(f"Generating email digest with top {top_n} articles")
    
    article_details = [
        RankedArticleDetail(
            digest_id=a.digest_id,
            rank=a.rank,
            relevance_score=a.relevance_score,
            reasoning=a.reasoning,
            title=next((d["title"] for d in digests if d["id"] == a.digest_id), ""),
            summary=next((d["summary"] for d in digests if d["id"] == a.digest_id), ""),
            url=next((d["url"] for d in digests if d["id"] == a.digest_id), ""),
            article_type=next((d["article_type"] for d in digests if d["id"] == a.digest_id), "")
        )
        for a in ranked_articles
    ]
    
    email_digest = email_agent.create_email_digest_response(
        ranked_articles=article_details,
        total_ranked=len(ranked_articles),
        limit=top_n
    )
    
    logger.info("Email digest generated successfully")
    logger.info(f"\n=== Email Introduction ===")
    logger.info(email_digest.introduction.greeting)
    logger.info(f"\n{email_digest.introduction.introduction}")
    
    return email_digest


def send_digest_email(hours: int = 24, top_n: int = 10) -> dict:
    try:
        result = generate_email_digest(hours=hours, top_n=top_n)
        markdown_content = result.to_markdown()
        html_content = digest_to_html(result)
        
        subject = f"Daily AI News Digest - {result.introduction.greeting.split('for ')[-1] if 'for ' in result.introduction.greeting else 'Today'}"
        
        send_email(
            subject=subject,
            body_text=markdown_content,
            body_html=html_content
        )
        
        logger.info("Email sent successfully!")
        return {
            "success": True,
            "subject": subject,
            "articles_count": len(result.articles)
        }
    except ValueError as e:
        logger.error(f"Error sending email: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def build_user_profile_dict(user: User) -> dict:
    return {
        "name": user.name,
        "background": user.background or "",
        "expertise_level": user.expertise_level or "Intermediate",
        "interests": user.interests or [],
        "preferences": {
            "content_depth": user.content_depth or "Technical but accessible",
            "content_type": user.content_type or "Mix of research and applications",
            "format": "Concise summaries with key takeaways",
        },
    }


def get_user_digests(repo: Repository, user: User, hours: int) -> list:
    channel_ids = [c.channel_id for c in repo.get_user_channels(user.id)]
    return repo.get_unsent_digests_for_user(
        user_id=user.id,
        hours=hours,
        channel_ids=channel_ids if channel_ids else None,
    )


def send_digest_email_for_user(user: User, hours: int = 240, top_n: int = 10) -> dict:
    repo = Repository()
    profile = build_user_profile_dict(user)
    curator = CuratorAgent(profile)
    email_agent = EmailAgent(profile)

    digests = get_user_digests(repo, user, hours)
    if not digests:
        logger.info(f"No unsent digests for {user.email}, skipping")
        return {"success": True, "skipped": True, "user": user.email}

    logger.info(f"Ranking {len(digests)} digests for {user.email}")
    ranked_articles = curator.rank_digests(digests)
    if not ranked_articles:
        logger.error(f"CuratorAgent returned no ranked articles for {user.email}")
        return {"success": False, "error": "ranking failed", "user": user.email}

    article_details = [
        RankedArticleDetail(
            digest_id=a.digest_id,
            rank=a.rank,
            relevance_score=a.relevance_score,
            reasoning=a.reasoning,
            title=next((d["title"] for d in digests if d["id"] == a.digest_id), ""),
            summary=next((d["summary"] for d in digests if d["id"] == a.digest_id), ""),
            url=next((d["url"] for d in digests if d["id"] == a.digest_id), ""),
            article_type=next((d["article_type"] for d in digests if d["id"] == a.digest_id), "")
        )
        for a in ranked_articles
    ]

    email_digest = email_agent.create_email_digest_response(
        ranked_articles=article_details,
        total_ranked=len(ranked_articles),
        limit=top_n
    )

    html_content = digest_to_html(email_digest)
    markdown_content = email_digest.to_markdown()
    subject = f"Daily AI News Digest — {datetime.now().strftime('%B %d, %Y')}"

    send_email(
        subject=subject,
        body_text=markdown_content,
        body_html=html_content,
        recipients=[user.email],
    )

    sent_ids = [a.digest_id for a in email_digest.articles]
    repo.mark_digests_sent(user.id, sent_ids)

    logger.info(f"✓ Email sent to {user.email} ({len(email_digest.articles)} articles)")
    return {
        "success": True,
        "user": user.email,
        "articles_count": len(email_digest.articles),
    }


if __name__ == "__main__":
    result = send_digest_email(hours=24, top_n=10)
    if result["success"]:
        print("\n=== Email Digest Sent ===")
        print(f"Subject: {result['subject']}")
        print(f"Articles: {result['articles_count']}")
    else:
        print(f"Error: {result['error']}")