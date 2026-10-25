"""The admin API (K13.1): users, roles, broker accounts and each account's risk limits, without
code or a terminal. Admins change; risk users may read; everyone else gets 403.

    GET   /api/admin/roles                          the roles there are, with what each allows
    GET   /api/admin/users                          everyone, with roles, on/off, locked
    POST  /api/admin/users                          new user: a one-time password and the
                                                    authenticator link, each shown once
    PATCH /api/admin/users/{id}                     name, roles, on/off
    POST  /api/admin/users/{id}/unlock              clear failed logins
    POST  /api/admin/users/{id}/reset-password      a one-time password, changed at next login
    POST  /api/admin/users/{id}/reset-authenticator a new authenticator link (a lost phone)
    GET   /api/admin/accounts                       every broker account, running or why not
    POST  /api/admin/accounts                       new account, started at once when it can be
    PATCH /api/admin/accounts/{id}                  who else may trade it, on/off
    GET   /api/admin/limits/spec                    what can be set (the limits spec)
    GET   /api/admin/accounts/{id}/limits           the limits in force
    PUT   /api/admin/accounts/{id}/limits[?dry_run] new limits: what changes (looser or
                                                    tighter) and what it means for what is open

Rules kept on the server: there is always an active admin (the last one can't lose the role or
be turned off), an admin can't turn themselves off, a turned-off account takes only closing
trades, a new limit applies from the next order and never cancels anything. Every change is
audited with who made it and what it was before; passwords and secrets never are.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from laddrix.domain.accounts import ROLE_HELP, BrokerAccount, Role, User
from laddrix.platform import AccountRuntime, Platform
from laddrix.risk.limits_spec import (
    changes,
    limits_from_dict,
    limits_spec_dict,
    limits_to_dict,
    stored_limits,
    warnings,
)
from laddrix.server import security
from laddrix.server.users import LastAdmin, UserStore

ID = r"^[A-Za-z0-9][A-Za-z0-9-]{0,39}$"
RoleName = Literal["admin", "trader", "risk"]


class AccountStarter(Protocol):
    """How this server runs a broker account (the assembly knows its brokers)."""

    brokers: tuple[str, ...]  # the kinds an admin may add, e.g. ("paper",) or ("kite", "paper")
    not_running: dict[str, str]  # account id -> why it isn't running (at start or when added)

    def start(self, account: BrokerAccount, limits: dict | None) -> str | None:
        """Run it now; None when it runs, else why not (said to the admin)."""


@dataclass
class CallbackStarter:
    """An AccountStarter from a function (the assemblies: serve, demo)."""

    brokers: tuple[str, ...]
    run: Callable[[BrokerAccount, dict | None], str | None]
    not_running: dict[str, str] = field(default_factory=dict)

    def start(self, account: BrokerAccount, limits: dict | None) -> str | None:
        return self.run(account, limits)


class NewUserIn(BaseModel):
    id: str = Field(pattern=ID)
    name: str = Field(min_length=1, max_length=80)
    roles: list[RoleName] = Field(min_length=1)


class UserPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    roles: list[RoleName] | None = None
    active: bool | None = None


class NewAccountIn(BaseModel):
    id: str = Field(pattern=ID)
    broker: str = Field(min_length=1, max_length=20)
    broker_client_id: str = Field(pattern=r"^[A-Za-z0-9]{1,40}$")
    owner: str = Field(pattern=ID)
    traders: list[str] = Field(default_factory=list, max_length=50)


class AccountPatch(BaseModel):
    traders: list[str] | None = Field(default=None, max_length=50)
    active: bool | None = None


class LimitsIn(BaseModel):
    limits: dict[str, Any]


def _user_dict(entry: dict) -> dict[str, Any]:
    u: User = entry["user"]
    return {"id": u.id, "name": u.name, "roles": sorted(r.value for r in u.roles),
            "active": u.active, "must_change_password": u.must_change_password,
            "locked_until": entry["locked_until"], "failures": entry["failures"],
            "sessions": entry["sessions"]}  # fmt: skip


def admin_router(
    platform: Platform,
    users: UserStore,
    current_user: Callable[..., User],
    audit: Callable[[str, str, dict[str, Any], str], None],
    starter: AccountStarter | None,
    step_up: Callable[[Request, str], None] = lambda _request, _what: None,  # (#363)
) -> APIRouter:
    router = APIRouter(prefix="/api/admin")
    Me = Annotated[User, Depends(current_user)]

    def can_read(user: User) -> None:
        if not (user.has_role(Role.ADMIN) or user.has_role(Role.RISK)):
            raise HTTPException(403, "for admin and risk users")

    def can_change(user: User) -> None:
        if not user.has_role(Role.ADMIN):
            raise HTTPException(403, "only an admin can change this")

    def mine(user: User) -> list[dict]:
        """The users of the asker's own workspace: an admin never sees another firm's (#368)."""
        return [e for e in users.users() if e["user"].workspace == user.workspace]

    def entry_for(user_id: str, user: User) -> dict:
        found = next((e for e in mine(user) if e["user"].id == user_id), None)
        if found is None:  # another firm's user is answered like no user at all
            raise HTTPException(404, f"no user {user_id}")
        return found

    # --- users -------------------------------------------------------------------------------

    @router.get("/roles")
    async def list_roles(user: Me) -> list[dict[str, str]]:
        can_read(user)
        return [{"name": r.value, "help": ROLE_HELP[r]} for r in Role]

    @router.get("/users")
    async def list_users(user: Me) -> list[dict[str, Any]]:
        can_read(user)
        return [_user_dict(e) for e in mine(user)]

    @router.post("/users", status_code=201)
    async def create_user(body: NewUserIn, user: Me, request: Request) -> dict[str, Any]:
        can_change(user)
        step_up(request, "add a user")
        temporary = security.new_temporary_password()
        roles = {Role(r) for r in body.roles}
        try:
            created = users.create_user(
                body.id, body.name, temporary, roles, must_change=True, workspace=user.workspace
            )  # the admin's own firm
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        audit("ADMIN_USER_CREATED", f"{user.id} added user {body.id} ({', '.join(body.roles)})",
              {"by": user.id, "user_id": body.id, "roles": sorted(body.roles)}, "-")  # fmt: skip
        return {"user": _user_dict(entry_for(body.id, user)), "temporary_password": temporary,
                "totp_uri": created.totp_uri,
                "note": "shown once: give both to the user; they set their own password at "
                        "the first login"}  # fmt: skip

    @router.patch("/users/{user_id}")
    async def change_user(user_id: str, body: UserPatch, user: Me,
                          request: Request) -> dict[str, Any]:  # fmt: skip
        can_change(user)
        if user_id == user.id and body.active is False:
            raise HTTPException(409, "you can't turn yourself off: ask another admin")
        if body.roles is not None or body.active is True:  # what someone may do: privileged
            step_up(request, "change roles or turn a user on")
        entry_for(user_id, user)  # another firm's user: 404, nothing changed
        try:
            before, after = users.update_user(
                user_id, name=body.name, active=body.active,
                roles={Role(r) for r in body.roles} if body.roles is not None else None,
            )  # fmt: skip
        except KeyError:
            raise HTTPException(404, f"no user {user_id}") from None
        except LastAdmin as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        was = {"name": before.name, "roles": sorted(r.value for r in before.roles),
               "active": before.active}  # fmt: skip
        now = {"name": after.name, "roles": sorted(r.value for r in after.roles),
               "active": after.active}  # fmt: skip
        if was != now:
            said = ", ".join(f"{k} {was[k]} → {now[k]}" for k in was if was[k] != now[k])
            extra = {"by": user.id, "user_id": user_id, "before": was, "after": now}
            audit("ADMIN_USER_CHANGED", f"{user.id} changed {user_id}: {said}", extra, "-")
        return _user_dict(entry_for(user_id, user))

    @router.post("/users/{user_id}/unlock")
    async def unlock_user(user_id: str, user: Me) -> dict[str, Any]:
        can_change(user)
        entry_for(user_id, user)
        if users.unlock(user_id):
            audit("ADMIN_USER_UNLOCKED", f"{user.id} unlocked {user_id}",
                  {"by": user.id, "user_id": user_id}, "-")  # fmt: skip
        return _user_dict(entry_for(user_id, user))

    @router.post("/users/{user_id}/reset-password")
    async def reset_password(user_id: str, user: Me, request: Request) -> dict[str, Any]:
        can_change(user)
        step_up(request, "reset a password")
        entry_for(user_id, user)
        try:
            temporary = users.reset_password(user_id)
        except KeyError:
            raise HTTPException(404, f"no user {user_id}") from None
        audit("ADMIN_PASSWORD_RESET", f"{user.id} reset the password of {user_id}",
              {"by": user.id, "user_id": user_id}, "-")  # fmt: skip
        return {"temporary_password": temporary,
                "note": "shown once; they were logged out and must set a new password at the "
                        "next login"}  # fmt: skip

    @router.post("/users/{user_id}/reset-authenticator")
    async def reset_authenticator(user_id: str, user: Me, request: Request) -> dict[str, Any]:
        can_change(user)
        step_up(request, "reset an authenticator")
        entry_for(user_id, user)
        try:
            uri = users.reset_authenticator(user_id)
        except KeyError:
            raise HTTPException(404, f"no user {user_id}") from None
        audit("ADMIN_AUTHENTICATOR_RESET", f"{user.id} reset the authenticator of {user_id}",
              {"by": user.id, "user_id": user_id}, "-")  # fmt: skip
        return {"totp_uri": uri, "note": "shown once; the old codes no longer work"}

    # --- accounts ------------------------------------------------------------------------------

    def account_dict(account: BrokerAccount) -> dict[str, Any]:
        running = platform.account(account.id)
        return {"id": account.id, "broker": account.broker,
                "broker_client_id": account.broker_client_id, "owner": account.owner_user_id,
                "traders": sorted(account.traders), "active": account.active,
                "running": running is not None,
                "not_running": "" if running else why_not(account.id)}  # fmt: skip

    def why_not(account_id: str) -> str:
        known = starter.not_running.get(account_id) if starter else None
        return known or "starts after a restart"

    def account_or_404(account_id: str, user: User) -> BrokerAccount:
        account = users.get_account(account_id)
        if account is None or account.workspace != user.workspace:  # another firm's: none
            raise HTTPException(404, f"no account {account_id}")
        return account

    @router.get("/accounts")
    async def list_accounts(user: Me) -> dict[str, Any]:
        can_read(user)
        return {"accounts": [account_dict(a) for a in users.accounts()
                             if a.workspace == user.workspace],
                "brokers": list(starter.brokers) if starter else []}  # fmt: skip

    @router.post("/accounts", status_code=201)
    async def create_account(body: NewAccountIn, user: Me, request: Request) -> dict[str, Any]:
        can_change(user)
        step_up(request, "add an account")
        if starter is None or body.broker not in starter.brokers:
            can = ", ".join(starter.brokers) if starter else "none"
            raise HTTPException(422, f"this server can add {can} accounts, not {body.broker}")
        unknown = sorted({body.owner, *body.traders} - {e["user"].id for e in mine(user)})
        if unknown:
            raise HTTPException(422, f"no such user: {', '.join(unknown)}")
        account = BrokerAccount(body.id, body.broker, body.broker_client_id, body.owner,
                                frozenset(set(body.traders) - {body.owner}))  # fmt: skip
        try:
            users.add_account(account)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        why = starter.start(account, users.account_limits(account.id))
        if why:
            starter.not_running[account.id] = why
        audit("ADMIN_ACCOUNT_CREATED",
              f"{user.id} added account {account.id} ({account.broker} "
              f"{account.broker_client_id}, owner {account.owner_user_id})",
              {"by": user.id, "account_id": account.id, "broker": account.broker,
               "owner": account.owner_user_id, "traders": sorted(account.traders)},
              account.id)  # fmt: skip
        return account_dict(account)

    @router.patch("/accounts/{account_id}")
    async def change_account(account_id: str, body: AccountPatch, user: Me,
                             request: Request) -> dict[str, Any]:  # fmt: skip
        can_change(user)
        if body.traders is not None or body.active is True:  # turning off never asks
            step_up(request, "change who trades an account or turn it on")
        account_or_404(account_id, user)
        try:
            before, after = users.update_account(
                account_id, active=body.active,
                traders=set(body.traders) if body.traders is not None else None,
            )  # fmt: skip
        except KeyError:
            raise HTTPException(404, f"no account {account_id}") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        running = platform.account(account_id)
        if running is not None:
            running.set_active(after.active)
        was = {"traders": sorted(before.traders), "active": before.active}
        now = {"traders": sorted(after.traders), "active": after.active}
        if was != now:
            said = ", ".join(f"{k} {was[k]} → {now[k]}" for k in was if was[k] != now[k])
            audit("ADMIN_ACCOUNT_CHANGED", f"{user.id} changed account {account_id}: {said}",
                  {"by": user.id, "account_id": account_id, "before": was, "after": now},
                  account_id)  # fmt: skip
        return account_dict(after)

    # --- limits --------------------------------------------------------------------------------

    @router.get("/limits/spec")
    async def limits_spec(user: Me) -> dict[str, Any]:
        can_read(user)
        return limits_spec_dict()

    def in_force(account_id: str, user: User) -> tuple[Any, AccountRuntime | None]:
        account_or_404(account_id, user)
        running = platform.account(account_id)
        if running is not None:
            return running.risk.limits, running
        return stored_limits(users.account_limits(account_id)), None

    @router.get("/accounts/{account_id}/limits")
    async def get_limits(account_id: str, user: Me) -> dict[str, Any]:
        can_read(user)
        limits, running = in_force(account_id, user)
        return {"account_id": account_id, "limits": limits_to_dict(limits),
                "running": running is not None}  # fmt: skip

    @router.put("/accounts/{account_id}/limits")
    async def put_limits(account_id: str, body: LimitsIn, user: Me, request: Request,
                         dry_run: bool = False) -> dict[str, Any]:  # fmt: skip
        can_change(user)
        old, running = in_force(account_id, user)
        new, problems = limits_from_dict(body.limits)
        if new is not None:
            gone = sorted(i for i in new.blocked_instruments if platform.instruments.get(i) is None)
            if gone:
                problems = [f"Blocked contracts: no such contract {', '.join(gone)}"]
        if new is None or problems:
            raise HTTPException(422, "; ".join(problems))
        changed = changes(old, new)
        said = []
        if running is not None:
            book = running.positions
            held = {}
            for instrument_id, qty in book.net_by_instrument().items():
                instrument = platform.instruments.get(instrument_id)
                held[instrument_id] = (qty, instrument.lot_size if instrument else 1)
            said = warnings(new, positions=held, working_orders=len(running.orders.working()),
                            day_pnl_paise=book.day_pnl_paise())  # fmt: skip
        if any(c["looser"] for c in changed) and not dry_run:  # tightening never asks
            step_up(request, "loosen limits")
        if changed and not dry_run:
            users.set_account_limits(account_id, limits_to_dict(new), user.id)
            if running is not None:
                running.apply_limits(new)
            audit("ADMIN_LIMITS_CHANGED",
                  f"{user.id} changed the limits of {account_id}: "
                  + "; ".join(c["text"] for c in changed),
                  {"by": user.id, "account_id": account_id,
                   "changes": [{k: c[k] for k in ("name", "old", "new", "looser")}
                               for c in changed]}, account_id)  # fmt: skip
        return {"account_id": account_id, "limits": limits_to_dict(new), "changes": changed,
                "warnings": said, "applied": bool(changed) and not dry_run,
                "running": running is not None}  # fmt: skip

    return router
