"""
Post 100 test comments on an Instagram post via the Graph API.

Usage:
    python seed_comments.py <instagram_post_id> <access_token>

Example:
    python seed_comments.py 17854360229135492 EAABsbCS...

The post ID is the numeric Instagram media ID (not the DB UUID).
You can grab your access token from the DB:
    SELECT instagram_access_token FROM users WHERE email = 'your@email.com';
"""

import asyncio
import sys
import httpx

GRAPH_URL = "https://graph.instagram.com"

COMMENTS = [
    # Positive / praise
    "This is absolutely amazing! 😍",
    "Love this so much! ❤️",
    "You never disappoint! Keep it up 🙌",
    "This made my day honestly",
    "One of your best posts yet!",
    "Stunning work as always ✨",
    "This is giving me all the vibes 🔥",
    "Obsessed with this content!",
    "You are so talented omg",
    "This deserves way more likes fr",

    # Questions
    "What camera did you use for this?",
    "Where was this taken? It looks incredible",
    "Can you share more behind the scenes?",
    "How long did this take to make?",
    "Do you have a tutorial for this?",
    "What editing app do you use?",
    "Are you selling prints of this?",
    "How do you come up with your ideas?",
    "Will you do a collab?",
    "When is your next post dropping?",

    # Negative / critical
    "Not your best work tbh",
    "I expected better from you",
    "This feels a bit rushed",
    "Not feeling this one sorry",
    "The old content was better",
    "Kinda disappointing to be honest",
    "Lost the spark a little",
    "This isn't really my thing",
    "Seems like you're running out of ideas",
    "I hope the next one is better",

    # Emoji-heavy
    "🔥🔥🔥🔥🔥",
    "❤️💛💚💙💜",
    "😱😱 no way this is real",
    "💯💯 this is everything",
    "🙏🙏🙏 thank you for this",
    "👏👏👏 well deserved",
    "😂😂 this is too good",
    "🤩🤩 absolutely floored",
    "💫⭐🌟✨ shining as always",
    "🎉🎊 congratulations on this!",

    # Hashtag-style
    "Love the #aesthetic here",
    "Pure #goals right here",
    "This is #trending for a reason",
    "Total #vibes with this one",
    "#Inspo for my whole week",
    "Straight up #art",
    "This is giving #main character energy",
    "#Blessed to see this on my feed",
    "Absolutely #iconic post",
    "Living for this #content",

    # Suggestions / feedback
    "You should post more of this type of content",
    "The lighting could be a bit better",
    "Would love to see a video version of this",
    "The caption really made this for me",
    "Next time try a different angle maybe?",
    "The colors in this are so well done",
    "I think this would do great as a reel",
    "The composition is chef's kiss 🤌",
    "Could you add subtitles next time?",
    "The music choice was perfect",

    # Fan reactions
    "Showed this to my friends and they all loved it",
    "Screenshotted this immediately",
    "I keep coming back to this post",
    "This is my new wallpaper 🥰",
    "Sharing this with everyone I know",
    "This is living rent free in my head",
    "Sent this to my whole group chat",
    "My mom even loves this one lol",
    "I've watched this 10 times already",
    "This is going straight to my saved folder",

    # Short one-liners
    "Wow.",
    "Speechless.",
    "Iconic.",
    "Perfection.",
    "Unreal.",
    "Needed this today.",
    "Always delivering.",
    "Never misses.",
    "Peak content.",
    "This it.",

    # Mixed / conversational
    "Okay but can we talk about how good this is?",
    "I wasn't ready for this honestly",
    "Every time you post I fall in love with your content all over again",
    "Not me saving this for the 5th time",
    "The way this hit different today",
    "I showed this to my sister and she follows you now lol",
    "Been following you for 2 years and you keep getting better",
    "Okay the way I screamed when this popped up on my feed",
    "This is the content I signed up for",
    "You really said let me ruin everyone's mood (in the best way)",

    # Supportive / encouraging
    "Please never stop posting",
    "The effort you put in really shows",
    "You inspire me every single day",
    "Keep going, you're doing amazing",
    "Your growth has been so beautiful to watch",
    "This community is so lucky to have you",
    "You put so much love into everything you make",
    "Hard work really does pay off, look at this!",
    "Your passion comes through in every post",
    "Thank you for sharing this with us 🙏",
]


async def post_comment(client: httpx.AsyncClient, post_id: str, token: str, message: str, index: int) -> bool:
    r = await client.post(
        f"{GRAPH_URL}/{post_id}/comments",
        params={"message": message, "access_token": token},
    )
    if r.status_code == 200:
        print(f"[{index+1:3}/100] ✓ {message[:60]}")
        return True
    else:
        print(f"[{index+1:3}/100] ✗ FAILED ({r.status_code}): {r.text[:100]}")
        return False


async def main(post_id: str, token: str):
    print(f"Posting 100 comments to post {post_id}...\n")
    ok = 0
    async with httpx.AsyncClient(timeout=30) as client:
        for i, comment in enumerate(COMMENTS):
            success = await post_comment(client, post_id, token, comment, i)
            if success:
                ok += 1
            # Small delay to avoid hitting rate limits
            await asyncio.sleep(0.5)

    print(f"\nDone: {ok}/100 comments posted successfully.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
