"""تست‌های روش‌های جست‌وجو: دیتابیس (ربات جستجو) / یوتیوب / ساوندکلاد."""
import pytest


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    dbfile = tmp_path / "search.db"
    monkeypatch.setenv("DB_PATH", str(dbfile))
    import importlib
    import config
    importlib.reload(config)
    from bot import database as db
    importlib.reload(db)
    db._conn = None
    yield db


# ================================================================ سه روش
def test_three_modes_no_both():
    """حالت «هر دو» حذف شد و «دیتابیس» جایش آمد."""
    from bot import platform_pref as pp

    assert pp.DATABASE == "database"
    assert pp._ORDER == [pp.DATABASE, pp.YOUTUBE, pp.SOUNDCLOUD]
    assert not hasattr(pp, "BOTH")


def test_default_is_database(fresh_db):
    from bot import platform_pref as pp

    assert pp.get(-1) == pp.DATABASE


def test_legacy_both_maps_to_database(fresh_db):
    """گروه‌هایی که در دیتابیس مقدار قدیمی `both` دارند نباید بشکنند."""
    from bot import platform_pref as pp

    fresh_db.group_set(-500, platform="both")
    assert fresh_db.group_get(-500)["platform"] == "both"
    assert pp.get(-500) == pp.DATABASE          # نگاشت خودکار


def test_cycle_order(fresh_db):
    from bot import platform_pref as pp

    cid = -600
    assert pp.cycle(cid) == pp.YOUTUBE
    assert pp.cycle(cid) == pp.SOUNDCLOUD
    assert pp.cycle(cid) == pp.DATABASE


def test_lock_overrides_choice(fresh_db):
    from bot import group_config as gc
    from bot import platform_pref as pp

    cid = -700
    gc.set_lock(cid, gc.LOCK_YOUTUBE)
    assert pp.effective(cid) == pp.YOUTUBE
    gc.set_lock(cid, gc.LOCK_SOUNDCLOUD)
    assert pp.effective(cid) == pp.SOUNDCLOUD
    gc.set_lock(cid, gc.LOCK_NONE)
    assert pp.effective(cid) == pp.get(cid)


def test_labels_persian(fresh_db):
    from bot import platform_pref as pp

    assert pp.label(-1) == "پلتفرم: دیتابیس"


# ================================================================ پنل
def test_panel_shows_three_methods(fresh_db):
    from bot import panel
    from bot import queue as q

    t = q.Track(title="آهنگ", stream_url="u", webpage_url="w", duration=100,
                duration_text="1:40", thumbnail=None, requester="A")
    panel.set_menu(-800, panel.MENU_PLAT)
    kb = panel.panel_keyboard(-800, t, 100, False)
    labels = [[b.text for b in r] for r in kb.inline_keyboard]
    assert ["دیتابیس", "یوتیوب", "ساوندکلاد"] in labels
    panel.set_menu(-800, None)


def test_database_icon_is_set():
    from bot import panel
    from bot import platform_pref as pp
    from bot import ui

    assert ui.EMO_DATABASE == "5890849007139296140"
    assert panel._PLAT_ICON[pp.DATABASE] == ui.EMO_DATABASE


def test_panel_label_database(fresh_db):
    from bot import panel

    assert panel.platform_label(-900) == "دیتابیس"


# ================================================================ ربات جستجو
def test_searchbot_disabled_without_group(monkeypatch):
    """بدون SEARCH_GROUP روش دیتابیس خاموش است و باید fallback شود."""
    import importlib

    monkeypatch.delenv("SEARCH_GROUP", raising=False)
    import config
    importlib.reload(config)
    from bot import searchbot
    importlib.reload(searchbot)
    assert searchbot.enabled() is False


def test_searchbot_enabled_with_group(monkeypatch):
    import importlib

    monkeypatch.setenv("SEARCH_GROUP", "-1001234567890")
    import config
    importlib.reload(config)
    from bot import searchbot
    importlib.reload(searchbot)
    assert searchbot.enabled() is True
    assert searchbot.group_id() == -1001234567890


def test_searchbot_default_username(monkeypatch):
    monkeypatch.delenv("SEARCH_BOT", raising=False)
    from bot import searchbot

    assert searchbot.bot_username() == "zandXmusicBot"

    monkeypatch.setenv("SEARCH_BOT", "@otherBot")
    assert searchbot.bot_username() == "otherBot"


def test_duration_parsing():
    from bot import searchbot

    assert searchbot._parse_duration("2:13") == 133
    assert searchbot._parse_duration("مدت 0:45 ثانیه") == 45
    assert searchbot._parse_duration("بدون عدد") == 0


def test_title_cleaning_and_artist_split():
    from bot import searchbot

    assert searchbot._clean_title("🎧  Gerye Kon Baram  ") == "Gerye Kon Baram"

    # فقط برای نتایجی که description ندارند و همه‌چیز در title است
    name, artist = searchbot._split_artist("Ali Navab - Gerye Kon")
    assert name == "Gerye Kon" and artist == "Ali Navab"

    name, artist = searchbot._split_artist("تک‌کلمه")
    assert name == "تک‌کلمه" and artist == ""


def test_result_shape_matches_live_bot():
    """ساختار واقعی @zandXmusicBot (با تست زنده تأیید شد):
    title = نام آهنگ · description = نام خواننده.
    """
    from bot import searchbot

    src = open("/opt/data/musicbot/bot/searchbot.py", encoding="utf-8").read()
    assert "title       = نام آهنگ" in src
    # عنوان نباید با خواننده قاتی شود وقتی description موجود است
    assert 'performer = _clean_title(desc)' in src


def test_search_returns_empty_when_disabled(monkeypatch):
    import asyncio
    import importlib

    monkeypatch.delenv("SEARCH_GROUP", raising=False)
    import config
    importlib.reload(config)
    from bot import searchbot
    importlib.reload(searchbot)

    assert asyncio.run(searchbot.search("هرچی")) == []
    assert asyncio.run(searchbot.fetch("هرچی")) is None


# ================================================================ تطبیق هوشمند
def test_skeleton_matches_persian_and_finglish():
    """اسکلت هم‌خوان‌ها: «دیوانه» ≡ «Divaneh»."""
    from bot import searchbot as sb

    assert sb._skeleton("دیوانه") == sb._skeleton("Divaneh")
    assert sb._skeleton("شادمهر") == sb._skeleton("Shadmehr")
    assert sb._skeleton("چاوشی") == sb._skeleton("Chavoshi")
    assert sb._skeleton("همراه") == sb._skeleton("Hamrah")


def test_skeleton_variants_cover_dropped_h_and_v():
    """«گریه»/«Gerye» و «خواب»/«Khab» با گونه‌ها تطبیق می‌خورند."""
    from bot import searchbot as sb

    assert sb._skels("گریه") & sb._skels("Gerye")
    assert sb._skels("خواب") & sb._skels("Khab")
    assert sb._skels("تتلو") & sb._skels("Tataloo")


def test_performer_words_excluded_from_title_score():
    """کلمه‌های خواننده نباید امتیاز نام آهنگ را رقیق کنند.

    باگ واقعی: «شادمهر عقیلی تماشا» با عنوان «Tamasha» فقط ۰.۳۳ می‌گرفت و
    رد می‌شد، چون دو کلمه‌ی دیگر اسم خواننده بودند.
    """
    from bot import searchbot as sb

    t, p = sb.parts("شادمهر عقیلی تماشا", "Tamasha", "Shadmehr Aghili")
    assert t > 0.9 and p > 0.5


def test_decide_picks_right_song_of_right_artist():
    from bot import searchbot as sb

    res = [{"title": "Mamnoon", "performer": "Shadmehr Aghili"},
           {"title": "Tamasha", "performer": "Shadmehr Aghili"}]
    i, conf = sb.decide("شادمهر عقیلی تماشا", res)
    assert conf is True and i == 1


def test_decide_no_guess_when_artist_lacks_song():
    """تصمیم کاربر: آهنگ هم‌نام از خواننده‌ی دیگر انتخاب نشود و به یوتیوب هم
    fallback نکند."""
    from bot import searchbot as sb

    res = [{"title": "Mamnoon", "performer": "Shadmehr Aghili"},
           {"title": "Divooneh", "performer": "Siavash Ghomayshi"}]
    i, conf = sb.decide("شادمهر عقیلی دیوانه", res)
    assert conf is False and i == 0


def test_decide_trusts_bot_order_on_vague_query():
    from bot import searchbot as sb

    res = [{"title": "Gerye Kon Baram", "performer": "Ali Navab"},
           {"title": "Nefrin", "performer": "Artoush"}]
    i, conf = sb.decide("الان گریه کن برام", res)
    assert conf is True and i == 0


def test_decide_empty_results():
    from bot import searchbot as sb

    assert sb.decide("هرچی", []) == (0, True)


def test_best_index_wraps_decide():
    from bot import searchbot as sb

    res = [{"title": "A", "performer": "X"}, {"title": "Tamasha",
                                              "performer": "Shadmehr Aghili"}]
    assert sb.best_index("شادمهر عقیلی تماشا", res) == 1


def test_database_mode_has_no_youtube_fallback():
    """روش دیتابیس نباید به یوتیوب برود (تصمیم کاربر)."""
    src = open("/opt/data/musicbot/bot/plugins/play.py", encoding="utf-8").read()
    i = src.index("روش دیتابیس (ربات جستجو)")
    j = src.index("# ---------- ساوندکلاد", i)
    block = src[i:j]
    assert "not_found_database" in block
    assert "fallback به یوتیوب" not in block
    assert "return None" in block


def test_not_found_database_message():
    from bot import messages as msg

    text, ents, kb = msg.not_found_database("شادمهر دیوانه")
    assert "در دیتابیس پیدا نشد" in text
    assert "شادمهر دیوانه" in text
    assert "پلتفرم" in text              # راهنمای عوض کردن روش
    total = len(text.encode("utf-16-le")) // 2
    for e in ents:
        assert e.offset + e.length <= total


def test_timeout_configurable(monkeypatch):
    from bot import searchbot

    monkeypatch.setenv("SEARCH_TIMEOUT", "40")
    assert searchbot.timeout() == 40.0
    monkeypatch.setenv("SEARCH_TIMEOUT", "بد")
    assert searchbot.timeout() == 25.0


# ================================================================ مسیر پخش
def test_track_has_performer_and_source():
    from bot import queue as q

    t = q.Track(title="t", stream_url="u", webpage_url="w", duration=1,
                duration_text="0:01", thumbnail=None, requester="A",
                performer="خواننده", source="searchbot")
    assert t.performer == "خواننده"
    assert t.source == "searchbot"


def test_archive_info_shape(fresh_db):
    from bot.plugins.play import _archive_info

    rec = {"title": "آهنگ آرشیو", "duration": 200, "url": "https://x"}
    info = _archive_info(rec, "fallback", "vid1")
    assert info["source"] == "archive"
    assert info["stream_url"] == "archive"
    assert info["title"] == "آهنگ آرشیو"
    assert info["id"] == "vid1"
    assert info["archive_rec"] is rec

    info2 = _archive_info({}, "پشتیبان")
    assert info2["title"] == "پشتیبان"
    assert info2["id"] == "q:پشتیبان"


def test_search_signature_takes_client():
    """_search باید client بگیرد (برای پیام خطا و روش دیتابیس)."""
    import inspect

    from bot.plugins import play

    params = list(inspect.signature(play._search).parameters)
    assert params == ["chat_id", "query", "is_video", "status", "client"]


def test_config_has_search_group():
    import config

    assert hasattr(config, "SEARCH_GROUP")
    assert isinstance(config.SEARCH_GROUP, int)


def test_searchbot_source_handled_in_player():
    """player باید منبع searchbot را بشناسد و آرشیو پس‌زمینه داشته باشد."""
    src = open("/opt/data/musicbot/bot/player.py", encoding="utf-8").read()
    assert 'track.source == "searchbot"' in src
    assert "_archive_searchbot" in src


def test_youtube_mode_does_not_try_soundcloud():
    """روش یوتیوب باید مستقیم یوتیوب باشد — بدون شعبه‌ی ساوندکلاد."""
    src = open("/opt/data/musicbot/bot/plugins/play.py", encoding="utf-8").read()
    i = src.index("async def _search(")
    j = src.index("async def _handle_play(", i)
    body = src[i:j]
    # شعبه‌ی ساوندکلاد فقط داخل بلوک mode == SOUNDCLOUD باشد
    sc_branch = body.index("platform_pref.SOUNDCLOUD")
    yt_tail = body.index("# ---------- یوتیوب")
    assert sc_branch < yt_tail
    assert body[yt_tail:].count("soundcloud.search") == 0


def test_all_modes_check_archive_first():
    """هر سه روش اول کانال دیتابیس را چک می‌کنند."""
    src = open("/opt/data/musicbot/bot/plugins/play.py", encoding="utf-8").read()
    i = src.index("async def _search(")
    j = src.index("async def _handle_play(", i)
    body = src[i:j]
    shared = body.index("گام صفر (مشترک)")
    db_branch = body.index("platform_pref.DATABASE")
    assert shared < db_branch          # چک آرشیو قبل از انتخاب روش
