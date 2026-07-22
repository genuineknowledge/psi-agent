"""Profile tools for the Haitun learning coach.

Only ``profile_update`` is exposed. Normal turns are recorded by the runtime
after-turn hook; this tool remains available for manual repair or diagnostics.
Profiles are aggregated by knowledge point and injected into the next turn's
system prompt, without retaining raw conversation transcripts.
"""

from __future__ import annotations

import json

from _user_profile import get_profile


async def profile_update(user_msg: str, agent_msg: str) -> str:
    """Update the learner profile with the latest exchange and save it.

    Call this at the very end of each turn, after you have composed your final
    reply.  Pass the user's exact message and your exact reply so the profile
    engine can detect depth, goal, and familiarity signals.

    Args:
        user_msg: The user's last message, verbatim.
        agent_msg: Your last reply, verbatim.
    Returns a JSON object with ``ok`` and the updated ``dimensions``.
    """
    profile = await get_profile()
    topic_key = profile.update(user_msg, agent_msg)
    await profile.save()
    topic = profile.topics[topic_key]
    return json.dumps(
        {
            "ok": True,
            "topic": topic["label"],
            "turns": topic["turns"],
            "dimensions": topic["dimensions"],
        },
        ensure_ascii=False,
    )
