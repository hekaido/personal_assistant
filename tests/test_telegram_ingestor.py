import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from telethon.tl.types import Channel, Chat, User

from assistant.ingestion.telegram_ingestor import classify_chat_type


def _mk_user(bot: bool) -> User:
    return User(
        id=1, is_self=False, contact=False, mutual_contact=False, deleted=False, bot=bot,
        bot_chat_history=False, bot_nochats=False, verified=False, restricted=False, min=False,
        bot_inline_geo=False, support=False, scam=False, apply_min_photo=False, fake=False,
        bot_attach_menu=False, premium=False, attach_menu_enabled=False, bot_can_edit=False,
        close_friend=False, stories_hidden=False, stories_unavailable=False,
        contact_require_premium=False, bot_business=False, bot_has_main_app=False, first_name="X",
    )


def _mk_channel(broadcast: bool, megagroup: bool) -> Channel:
    return Channel(
        id=2, title="X", photo=None, date=None, creator=False, left=False, broadcast=broadcast,
        verified=False, megagroup=megagroup, restricted=False, signatures=False, min=False, scam=False,
        has_link=False, has_geo=False, slowmode_enabled=False, call_active=False, call_not_empty=False,
        fake=False, gigagroup=False, noforwards=False, join_to_send=False, join_request=False, forum=False,
        access_hash=None, username=None, restriction_reason=None, admin_rights=None, banned_rights=None,
        default_banned_rights=None, participants_count=None,
    )


def test_classify_chat_type():
    assert classify_chat_type(_mk_user(bot=False)) == "user"
    assert classify_chat_type(_mk_user(bot=True)) == "bot"
    assert classify_chat_type(Chat(id=3, title="X", photo=None, participants_count=0, date=None, version=0)) == "group"
    assert classify_chat_type(_mk_channel(broadcast=True, megagroup=False)) == "channel"
    assert classify_chat_type(_mk_channel(broadcast=False, megagroup=True)) == "group"


if __name__ == "__main__":
    test_classify_chat_type()
    print("Все тесты classify_chat_type прошли")
