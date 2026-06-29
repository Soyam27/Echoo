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
    # Positive
    "This turned out so clean!",
    "Absolutely nailed it 👏",
    "Can't stop looking at this.",
    "This is next level 🔥",
    "Everything about this is perfect.",
    "You keep raising the bar.",
    "Instant like from me ❤️",
    "This deserves to go viral.",
    "Such a creative idea!",
    "You really outdid yourself this time.",

    # Questions
    "How did you shoot this?",
    "Which software did you use?",
    "Is this available in 4K?",
    "Can you make a tutorial?",
    "Where can I find more like this?",
    "What's your workflow like?",
    "Did you edit this yourself?",
    "Any tips for beginners?",
    "How many attempts did this take?",
    "Will there be a part two?",

    # Constructive
    "Maybe brighten the shadows a little.",
    "A different soundtrack could work better.",
    "Would love a longer version.",
    "The pacing felt slightly fast.",
    "Maybe zoom in on the details next time.",
    "The ending felt a bit abrupt.",
    "Would look even better in landscape.",
    "The intro could be stronger.",
    "A voice-over would be awesome.",
    "Consider adding captions for accessibility.",

    # Funny
    "My jaw is still on the floor 😂",
    "How is this even legal??",
    "You woke up and chose excellence.",
    "This called me broke somehow 😭",
    "Respectfully... I'm obsessed.",
    "I wasn't emotionally prepared for this.",
    "Bro really cooked 🔥",
    "Okay this is unfairly good.",
    "Who gave you permission to be this talented?",
    "I need this injected into my veins.",

    # Emojis
    "😍😍😍",
    "🔥👏❤️",
    "🤯🤯🤯",
    "💯",
    "✨✨✨",
    "🙌🙌🙌",
    "🥹❤️",
    "😎👌",
    "👏👏👏👏",
    "🎉🔥",

    # Hashtags
    "#masterpiece",
    "#creative",
    "#goals",
    "#worthit",
    "#qualitycontent",
    "#loveit",
    "#dailyinspo",
    "#beautifulwork",
    "#artist",
    "#mustsee",

    # Fan reactions
    "Saved this immediately.",
    "This is now in my favorites.",
    "Sending this to my best friend.",
    "I keep replaying this.",
    "This deserves millions of views.",
    "My whole family loved this.",
    "This made my evening.",
    "Adding this to my inspiration board.",
    "Can't believe this is free to watch.",
    "This deserves an award.",

    # Short comments
    "Phenomenal.",
    "Legendary.",
    "Insane.",
    "Beautiful.",
    "Brilliant.",
    "Clean.",
    "Underrated.",
    "Outstanding.",
    "Excellent.",
    "Wow!",

    # Conversational
    "I honestly didn't expect it to be this good.",
    "Every post somehow gets better.",
    "You've gained a lifelong fan.",
    "I wish more creators put in this much effort.",
    "This is exactly what I needed today.",
    "You always deliver quality.",
    "Can't wait to see what comes next.",
    "This deserves all the recognition.",
    "I appreciate the attention to detail.",
    "You're setting the standard.",

    # Mixed sentiment
    "Pretty good overall!",
    "Nice work, just a little too short.",
    "The visuals are amazing but the audio could improve.",
    "Loved everything except the ending.",
    "Definitely one of your stronger posts.",
    "Interesting concept!",
    "Good job, keep experimenting.",
    "This one grew on me after a second watch.",
    "Solid effort 👏",
    "Looking forward to your next upload."
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
