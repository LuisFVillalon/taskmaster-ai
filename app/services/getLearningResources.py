import json
import os
import re
import urllib.parse
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL_NAME = os.getenv("OPENAI_MODEL")

# The LLM names specific, real resources — not search queries.
# Python builds targeted search URLs using the exact name + creator/publication
# so the user lands within one click of the actual resource, with zero hallucinated URLs.
_SYSTEM = (
    "You are a Curated Scout — a knowledgeable learning advisor for any domain. "
    "You recommend specific, real, named resources that actually exist. "
    "Return ONLY valid JSON — no markdown fences, no explanation, no extra keys. "
    'Schema: {"topic":string,'
    '"video_direct_name":string,"video_creator":string,"video_why":string,'
    '"article_direct_name":string,"article_publication":string,"article_why":string,'
    '"activity_label":string,"exercise_direct_name":string,"exercise_platform":string,"exercise_why":string}'
)

_USER_PROMPT = (
    "Analyze these notes:\n\n{note_content}\n\n"
    "Identify the core theme (e.g., 'Renaissance Art', 'Project Management', 'Quantum Physics'). "
    "Act as a knowledgeable scout and recommend three specific, real resources:\n\n"
    "1. VIDEO — name one specific, well-known video or series and its creator/channel. "
    "Examples: '3Blue1Brown\\'s Essence of Calculus' by '3Blue1Brown', "
    "'Crash Course Art History' by 'CrashCourse', "
    "'The Lean Startup' talk by 'Eric Ries at Google Talks'. "
    "Set video_direct_name to the exact video/series title and video_creator to the channel or speaker.\n\n"
    "2. ARTICLE — name one specific, real article title and its publication. "
    "Examples: 'The Innovator\\'s Dilemma' summary by 'Harvard Business Review', "
    "'Introduction to Renaissance Art' by 'Smarthistory', "
    "'What Is Machine Learning?' by 'MIT Technology Review'. "
    "Prefer authoritative domain publications over general encyclopedias. "
    "Set article_direct_name to the exact article or piece title and article_publication to the outlet.\n\n"
    "3. EXERCISE — name one specific interactive tool, course module, or platform exercise. "
    "Examples: 'Introduction to GitHub' on 'GitHub Learning Lab', "
    "'Honing a Knife' lesson on 'MasterClass', "
    "'Binary Search' problem set on 'LeetCode', "
    "'Linear Algebra' course on 'Khan Academy', "
    "'Business Strategy' simulation on 'Coursera'. "
    "Set exercise_direct_name to the exact exercise/module title, exercise_platform to the platform name, "
    "and activity_label to a short domain-specific action word (e.g., 'Coding Challenge', "
    "'Technique Drill', 'Case Study', 'Writing Prompt', 'Virtual Lab', 'Grammar Exercise').\n\n"
    "Keep every 'why' field under 15 words, imperative coaching tone, tailored to these specific notes."
)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _exercise_url(name: str, platform: str) -> str:
    """
    Route to the named platform's own search when recognised.
    Falls back to a quoted-title Google search so the user lands as close
    as possible to the specific resource without using a hallucinated URL.
    """
    p = platform.lower()
    q = urllib.parse.quote_plus(name)
    if "github" in p:
        return f"https://github.com/search?q={q}&type=repositories"
    if "khan" in p:
        return f"https://www.khanacademy.org/search?page_search_query={q}"
    if "leetcode" in p:
        return f"https://leetcode.com/problemset/?search={q}"
    if "codecademy" in p:
        return f"https://www.codecademy.com/search?query={q}"
    if "coursera" in p:
        return f"https://www.coursera.org/search?query={q}"
    if "masterclass" in p:
        return f"https://www.masterclass.com/search?q={q}"
    if "quizlet" in p:
        return f"https://quizlet.com/search?query={q}&type=sets"
    if "brilliant" in p:
        return f"https://brilliant.org/search/?q={q}"
    if "duolingo" in p:
        return f"https://www.duolingo.com/search?q={q}"
    # Unknown platform: quoted title + platform name on Google for maximum precision
    fallback = urllib.parse.quote_plus(f'"{name}" {platform}')
    return f"https://www.google.com/search?q={fallback}"


def _build_resources(data: dict) -> list[dict]:
    """
    Construct targeted search URLs from the LLM's specific, named resource recommendations.

    URL strategy — each URL is designed to surface the exact resource within one click:
      Video    → YouTube search for "{direct_name} {creator}" (first result is typically exact)
      Article  → Google quoted-title search: "{direct_name}" {publication}
      Exercise → Platform-specific search URL for known platforms; quoted Google fallback otherwise
    """
    video_name    = data.get("video_direct_name", "")
    video_creator = data.get("video_creator", "")
    article_name  = data.get("article_direct_name", "")
    article_pub   = data.get("article_publication", "")
    exercise_name = data.get("exercise_direct_name", "")
    exercise_plat = data.get("exercise_platform", "")

    # Video: combine name + creator for maximum YouTube targeting
    vq = urllib.parse.quote_plus(f"{video_name} {video_creator}".strip())
    video_url = f"https://www.youtube.com/results?search_query={vq}"

    # Article: quoted exact title + publication name → Google typically shows the exact page first
    aq = urllib.parse.quote_plus(f'"{article_name}" {article_pub}'.strip())
    article_url = f"https://www.google.com/search?q={aq}"

    exercise_url = _exercise_url(exercise_name, exercise_plat)

    return [
        {
            "type": "video",
            "title": video_name or "Video Resource",
            "url": video_url,
            "why": data.get("video_why", ""),
            "platform": video_creator or "YouTube",
            "activity_label": "Watch",
        },
        {
            "type": "article",
            "title": article_name or "Article",
            "url": article_url,
            "why": data.get("article_why", ""),
            "platform": article_pub or "Web",
            "activity_label": "Read",
        },
        {
            "type": "exercise",
            "title": exercise_name or "Interactive Exercise",
            "url": exercise_url,
            "why": data.get("exercise_why", ""),
            "platform": exercise_plat or "Practice",
            "activity_label": data.get("activity_label") or "Practice",
        },
    ]


async def get_learning_resources(note_content: str) -> dict:
    """
    Extract the core topic from note content and return 3 curated learning resources.

    The LLM names specific, real resources (video title + creator, article title + publication,
    exercise name + platform). Python builds targeted search URLs from those names so the user
    lands within one click of the exact resource — no hallucinated URLs, no generic search pages.
    """
    clean = _strip_html(note_content)[:1500]

    if len(clean) < 20:
        return {"topic": "", "resources": []}

    response = await client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _USER_PROMPT.format(note_content=clean)},
        ],
        temperature=0.3,
        max_tokens=500,
        response_format={"type": "json_object"},
    )

    raw = (response.choices[0].message.content or "{}").strip()
    data = json.loads(raw)

    return {
        "topic": data.get("topic", ""),
        "resources": _build_resources(data),
    }
