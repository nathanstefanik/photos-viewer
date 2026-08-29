import uuid

import pytest

from app.social_store import (
    SocialStore,
    default_display_name,
    is_single_emoji,
    new_guest_id,
    normalize_display_name,
)


@pytest.fixture
def store(tmp_path):
    return SocialStore(str(tmp_path / "social.db"))


def test_default_display_name_format():
    name = default_display_name()
    assert name.startswith("Guest-")
    assert len(name) == len("Guest-") + 4


def test_new_guest_id_is_uuid():
    uuid.UUID(new_guest_id())  # raises ValueError if malformed


@pytest.mark.parametrize("emoji", ["\U0001F600", "\U0001F44D", "\U0001F1FA\U0001F1F8", "❤️"])
def test_is_single_emoji_accepts(emoji):
    assert is_single_emoji(emoji)


@pytest.mark.parametrize("value", ["a", "ab", "", "hello", "<script>", "\U0001F600\U0001F600"])
def test_is_single_emoji_rejects(value):
    assert not is_single_emoji(value)


def test_normalize_display_name_fallback_on_empty():
    assert normalize_display_name("", "fallback") == "fallback"
    assert normalize_display_name(None, "fallback") == "fallback"


def test_normalize_display_name_strips_and_collapses_whitespace():
    assert normalize_display_name("  Jane   Doe  ", "fallback") == "Jane Doe"


def test_normalize_display_name_rejects_disallowed_chars():
    assert normalize_display_name("<script>alert(1)</script>", "fallback") == "fallback"


def test_normalize_display_name_rejects_overlong_input():
    # The length cap lives in the validation regex, not a separate truncation step;
    # anything over 64 chars fails to match and falls back rather than being truncated.
    assert normalize_display_name("A" * 100, "fallback") == "fallback"


def test_reaction_toggle_add_and_remove(store):
    reactions = store.toggle_reaction("asset-1", "guest-1", "\U0001F600", "Alice")
    assert len(reactions) == 1
    assert reactions[0].count == 1
    assert reactions[0].reacted is True

    reactions = store.toggle_reaction("asset-1", "guest-1", "\U0001F600", "Alice")
    assert reactions == []


def test_reaction_toggle_rejects_non_emoji(store):
    with pytest.raises(ValueError):
        store.toggle_reaction("asset-1", "guest-1", "not-emoji", "Alice")


def test_reaction_groups_by_emoji_across_guests(store):
    store.toggle_reaction("asset-1", "guest-1", "\U0001F600", "Alice")
    store.toggle_reaction("asset-1", "guest-2", "\U0001F600", "Bob")
    reactions = store.list_reactions("asset-1", "guest-1")
    assert len(reactions) == 1
    assert reactions[0].count == 2
    assert reactions[0].reacted is True
    assert set(reactions[0].names) == {"Alice", "Bob"}


def test_comments_add_list_delete(store):
    comment = store.add_comment("asset-1", "guest-1", "Hello!", "Alice")
    assert comment.mine is True

    comments = store.list_comments("asset-1", "guest-2")
    assert len(comments) == 1
    assert comments[0].mine is False

    assert store.delete_comment(comment.id, "guest-2") is False  # not the author
    assert store.delete_comment(comment.id, "guest-1") is True
    assert store.list_comments("asset-1", "guest-1") == []


def test_comment_empty_rejected(store):
    with pytest.raises(ValueError):
        store.add_comment("asset-1", "guest-1", "   ", "Alice")


def test_comment_too_long_rejected(store):
    with pytest.raises(ValueError):
        store.add_comment("asset-1", "guest-1", "x" * 1001, "Alice")
