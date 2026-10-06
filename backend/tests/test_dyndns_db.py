"""DynDNS/PTR gegen MariaDB (F9 9 test_dyndns_db; ``requires_db``, Fixture ``fresh_db``).

Tabelle ``dyndns_tokens`` (A.1 inkl. ``stale_servers`` [D5]), Token-Roundtrip mit ``last_used_at``,
``delete_tokens_of_user`` (fuer ``delete_user``, WS-F10-APP-BE), Settings ``ptr_auto_default`` und die
Schreibrechts-Filter mit echten ``user_zone_access``-Zeilen.
"""
import asyncio

from sqlalchemy import select, text

from dbutil import requires_db

pytestmark = requires_db


def _run(coro):
    return asyncio.run(coro)


async def _user(s, name, role="user", active=True):
    from app.core.auth import hash_password
    from app.models.models import User

    u = User(username=name, hashed_password=hash_password("x-passwort-1"), role=role, is_active=active,
             display_name=name)
    s.add(u)
    await s.flush()
    return u


def test_init_db_twice_and_columns(fresh_db):
    from app.core import database

    async def go():
        database.MIGRATION_ERRORS.clear()
        await database.init_db()
        assert database.MIGRATION_ERRORS == []
        async with fresh_db.connect() as conn:
            cols = {r[0] for r in (await conn.execute(text("SHOW COLUMNS FROM dyndns_tokens"))).all()}
            idx = (await conn.execute(text("SHOW INDEX FROM dyndns_tokens"))).all()
        return cols, idx

    cols, idx = _run(go())
    assert {"id", "user_id", "name", "token_prefix", "token_hash", "hostnames", "allowed_types", "ttl", "update_ptr",
            "is_active", "created_at", "updated_at", "last_used_at", "last_used_ip", "last_ip_v4", "last_ip_v6",
            "last_result", "last_changed_at", "stale_servers"} <= cols
    unique = {r[2] for r in idx if r[1] == 0}  # Non_unique == 0
    assert any("token_hash" in name for name in unique)


def test_token_roundtrip_and_stale_survives_restart(fresh_db):
    from app.core.database import async_session
    from app.models.models import DynDnsToken
    from app.services import dyndns

    async def go():
        async with async_session() as s:
            owner = await _user(s, "ddns_owner")
            t, plain = await dyndns.create_token(s, user=owner, name="Router", hostnames=["home.example.com"],
                                                 allowed_types=["A"], ttl=120, update_ptr=True)
            await s.commit()
            tid, uid = t.id, owner.id
        async with async_session() as s:
            row = (await s.execute(select(DynDnsToken).where(DynDnsToken.id == tid))).scalar_one()
            assert row.token_hash == dyndns.hash_token(plain) and plain not in str(row.__dict__)
            assert row.hostnames == ["home.example.com."] and row.allowed_types == ["A"]
            assert row.update_ptr is True and row.is_active is True and row.last_used_at is None
            found = await dyndns.verify_token(s, plain, remote_ip="203.0.113.9")
            assert found is not None and found.id == tid
            assert found.last_used_at is not None and found.last_used_ip == "203.0.113.9"
            assert (await dyndns.load_owner(s, found)).id == uid
            # [D5] veraltete Server persistent
            found.stale_servers = {"home.example.com.": ["ns2"]}
            await s.commit()
        async with async_session() as s:
            row = (await s.execute(select(DynDnsToken).where(DynDnsToken.id == tid))).scalar_one()
            assert row.stale_servers == {"home.example.com.": ["ns2"]}
            assert dyndns.serialize_token(row)["stale_servers"] == ["ns2"]
            assert await dyndns.verify_token(s, dyndns.TOKEN_PREFIX + "falsch", remote_ip=None) is None
            row.is_active = False
            await s.commit()
        async with async_session() as s:
            assert await dyndns.verify_token(s, plain, remote_ip=None) is None
            assert (await dyndns.find_token_by_plaintext(s, plain)).id == tid
            assert await dyndns.count_tokens_of_user(s, uid) == 1

    _run(go())


def test_owner_inactive_and_delete_tokens_of_user(fresh_db):
    from app.core.database import async_session
    from app.services import dyndns

    async def go():
        async with async_session() as s:
            owner = await _user(s, "ddns_del", active=False)
            other = await _user(s, "ddns_other")
            t1, _ = await dyndns.create_token(s, user=owner, name="a", hostnames=["a.example.com"],
                                              allowed_types=["A"], ttl=60, update_ptr=False)
            await dyndns.create_token(s, user=owner, name="b", hostnames=["b.example.com"],
                                      allowed_types=["A"], ttl=60, update_ptr=False)
            await dyndns.create_token(s, user=other, name="c", hostnames=["c.example.com"],
                                      allowed_types=["A"], ttl=60, update_ptr=False)
            await s.commit()
            assert await dyndns.load_owner(s, t1) is None  # Besitzer deaktiviert
            n = await dyndns.delete_tokens_of_user(s, owner.id)
            await s.commit()
            assert n == 2
            assert await dyndns.count_tokens_of_user(s, owner.id) == 0
            assert await dyndns.count_tokens_of_user(s, other.id) == 1

    _run(go())


def test_security_revoke_survives_reactivation(fresh_db):
    """Fix-Runde: Token im Query bzw. Zugangs-Widerruf entwerten das Secret in der DB; is_active=1 belebt den alten
    Klartext nicht wieder, erst rotate liefert einen nutzbaren Token."""
    from app.core.database import async_session
    from app.models.models import DynDnsToken
    from app.services import dyndns

    async def go():
        async with async_session() as s:
            owner = await _user(s, "ddns_revoke")
            t1, p1 = await dyndns.create_token(s, user=owner, name="q", hostnames=["a.example.com"],
                                               allowed_types=["A"], ttl=60, update_ptr=False)
            t2, p2 = await dyndns.create_token(s, user=owner, name="r", hostnames=["b.example.com"],
                                               allowed_types=["A"], ttl=60, update_ptr=False)
            t3, p3 = await dyndns.create_token(s, user=owner, name="p", hostnames=["c.example.com"],
                                               allowed_types=["A"], ttl=60, update_ptr=False)
            t3.is_active = False  # pausiert
            await s.commit()
            ids, uid = (t1.id, t2.id, t3.id), owner.id
        async with async_session() as s:
            assert await dyndns.revoke_tokens_in_query(s, [p1], client_ip="203.0.113.1") == 1
            await s.commit()
        async with async_session() as s:
            assert await dyndns.revoke_tokens_of_user(s, uid) == 2  # t2 + pausierter t3, t1 schon entwertet
            await s.commit()
        async with async_session() as s:
            rows = (await s.execute(select(DynDnsToken).where(DynDnsToken.id.in_(ids)))).scalars().all()
            assert len(rows) == 3 and all(dyndns.is_secret_revoked(r) and not r.is_active for r in rows)
            for r in rows:
                r.is_active = True  # Reaktivieren an der API vorbei
            await s.commit()
        async with async_session() as s:
            for plain in (p1, p2, p3):
                assert await dyndns.verify_token(s, plain, remote_ip=None) is None
                assert await dyndns.find_token_by_plaintext(s, plain) is None
            row = (await s.execute(select(DynDnsToken).where(DynDnsToken.id == ids[0]))).scalar_one()
            new_plain = await dyndns.rotate_token(s, row)
            await s.commit()
        async with async_session() as s:
            found = await dyndns.verify_token(s, new_plain, remote_ip=None)
            assert found is not None and found.id == ids[0] and not dyndns.is_secret_revoked(found)

    _run(go())


def test_settings_ptr_auto_default_and_dyndns_switches(fresh_db):
    from app.core.database import async_session
    from app.services import dyndns, ptr
    from app.services.system_settings import get_bool_setting, set_setting

    async def go():
        async with async_session() as s:
            assert await get_bool_setting(s, ptr.KEY_AUTO_DEFAULT, False) is False
            assert await dyndns.is_enabled(s) is True and await dyndns.allow_private(s) is False
            await set_setting(s, ptr.KEY_AUTO_DEFAULT, True)
            await set_setting(s, dyndns.KEY_ENABLED, False)
            await s.commit()
        async with async_session() as s:
            assert await ptr.resolve_manage_ptr(s, None) is True
            assert await ptr.resolve_manage_ptr(s, False) is False
            assert await dyndns.is_enabled(s) is False
            await set_setting(s, dyndns.KEY_ENABLED, True)
            await s.commit()

    _run(go())


def test_writable_zone_filter_with_acl_rows(fresh_db):
    from app.core.database import async_session
    from app.models.models import UserZoneAccess
    from app.services import ptr

    async def go():
        async with async_session() as s:
            u = await _user(s, "ptr_acl")
            admin = await _user(s, "ptr_admin", role="admin")
            s.add(UserZoneAccess(user_id=u.id, zone_name="2.0.192.in-addr.arpa.", permission="manage"))
            s.add(UserZoneAccess(user_id=u.id, zone_name="example.com.", permission="read"))
            s.add(UserZoneAccess(user_id=u.id, zone_name="legacy.example.", permission=None))
            await s.commit()
            assert await ptr.writable_zone_filter(s, u) == {"2.0.192.in-addr.arpa.", "legacy.example."}
            assert await ptr.writable_zone_filter(s, admin) is None

    _run(go())


def test_token_audit_written_with_zone_free_resource(fresh_db):
    """DYNDNS_TOKEN_*-Audits haben keinen zone_name (kein Leak in den Zonenverlauf)."""
    from app.core.database import async_session
    from app.models.models import AuditLog
    from app.services import dyndns
    from app.services.audit import write_audit

    async def go():
        async with async_session() as s:
            owner = await _user(s, "ddns_audit")
            t, plain = await dyndns.create_token(s, user=owner, name="r", hostnames=["home.example.com"],
                                                 allowed_types=["A", "AAAA"], ttl=60, update_ptr=False)
            await write_audit(s, "DYNDNS_TOKEN_CREATE", "dyndns_token", t.name, user_id=owner.id,
                              details=dyndns.audit_token_details(t))
            await s.commit()
            row = (await s.execute(select(AuditLog).where(AuditLog.action == "DYNDNS_TOKEN_CREATE"))).scalars().first()
            assert row.zone_name is None and plain not in str(row.details) and t.token_hash not in str(row.details)

    _run(go())
