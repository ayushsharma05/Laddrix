# Admin: users, roles, accounts and limits (K13)

## K13.1 The admin API (#281)

Before K13, users and accounts were added from the server's terminal (`tools/users.py`, which still works), and each account's risk limits were fixed when the server started. Now an admin does all of it from the API, and the K13.2 screen will draw itself from what the server publishes.

```
admin's screen ──PUT /api/admin/accounts/acc-1/limits──▶ check against the limits spec
                                                          │ refused? 422 with every reason
                                                          ▼
                                    changes (looser/tighter) + warnings for what is open
                                                          │ dry_run? stop here (a preview)
                                                          ▼
                       saved (users.db: account_limits) ──▶ AccountRuntime.apply_limits
                       audited ADMIN_LIMITS_CHANGED         risk checks · daily-loss stop ·
                                                            MTM and square-off closing pieces
```

### Who may do what
- **Admin:** everything below.
- **Risk:** reads users, accounts and limits. Can't change anything.
- **Others:** 403.
- Changing requests pass the same origin and JSON checks as every other request.

### Users (`/api/admin/users…`)
- **Create.** The server makes a one-time password and an authenticator link, each shown once.
  - The new user must set their own password before anything else.
  - Until then every route except `/api/me`, `/api/me/password` and logout answers 403, and the stream is refused.
- **Change:** name, roles, on/off. Turning a user off logs them out everywhere at once.
- **Unlock** after failed logins.
- **Reset the password:** a one-time password again, and the user is logged out.
- **Reset the authenticator** (a lost phone): the old codes stop working.
- **Guards, kept on the server:**
  - There is always an active admin. The last one can't lose the role or be turned off. This is checked under the store's lock, so two admins can't remove each other at the same moment.
  - An admin can't turn themselves off.
- **Every user's own change:** `POST /api/me/password` needs the old password, a new one of 12 or more characters, and different from the old. It ends that user's other sessions.

### Accounts (`/api/admin/accounts…`)
- **Create.** The server says which brokers it can run (`brokers`). A paper account runs at once:
  - `Platform` starts its loops even though `run()` is already going, and ends them with its own.
  - A Kite account starts after a restart, because its app key must be in the settings first. The listing says why an account isn't running.
- **Who else may trade it.** They must be users. The owner always may, so the owner isn't listed.
- **Turn it off or on:**
  - A turned-off account takes **only closing trades**. The risk gate checks every new order from any source (ticket, algo, GTT leg, basket): an order passes only if it reduces the position, counting what already works on that side.
  - Cancels always work.
  - Its traders still see it and can stop it, close it and cancel on it (`can_manage`). `/api/me` sends `active: false` so the screen can say so.

### Limits (`/api/admin/limits/spec`, `/api/admin/accounts/{id}/limits`)
- **The spec.** `risk/limits_spec.py` publishes every `RiskLimits` field in the algo-spec format: label, kind, range, default and help.
  - New kinds: `money` (paise, shown in rupees), `choices` (several of a list) and `instruments` (a list of contracts).
  - The price band has one field per kind: `price_band_pct.EQ`, `…FUT`, `…CE`, `…PE`.
  - A test makes sure no limit is ever missing from the spec.
- **PUT** checks everything the same way:
  - unknown names, kinds and ranges;
  - at least one exchange and one kind;
  - blocked contracts must exist.
- **The answer:**
  - `changes`: each changed limit with the old and new value in words, and whether the change **loosens** it. The screen asks twice before a loosening.
  - `warnings`: what a tighter limit means for what is open now. Examples: a position above the new position limit takes only reducing orders; too many working orders blocks new ones until some finish; a loss already beyond a new daily-loss limit stops trading at the next price change. **An edit never cancels or closes anything itself.**
  - `?dry_run=true` is a preview: nothing is saved or applied.
- **Applied from the next order** everywhere a limit had been copied at the start: the risk gate, the daily-loss stop, and the closing pieces of the MTM watch and the square-off. Working orders are left as they are.
- **Saved** in `users.db` (`account_limits`), so they come back after a restart (`serve`, `demo`).
  - A saved name a newer version no longer knows is dropped.
  - A value now out of range takes its default.
  - So a saved file never stops an account from starting (`stored_limits`).

### Audit
Every change is written with who made it and the before and after values:
- `ADMIN_USER_CREATED`, `ADMIN_USER_CHANGED`, `ADMIN_USER_UNLOCKED`;
- `ADMIN_PASSWORD_RESET`, `ADMIN_AUTHENTICATOR_RESET`, `PASSWORD_CHANGED`;
- `ADMIN_ACCOUNT_CREATED`, `ADMIN_ACCOUNT_CHANGED`;
- `ADMIN_LIMITS_CHANGED`.

Passwords and authenticator secrets are never written.

### Older files
An older `users.db` gets the new column (`must_change`) and table when the server opens it.

### Checks
- `tests/test_admin.py`:
  - the spec covers every limit and its defaults;
  - every kind of refusal;
  - saved limits from another version;
  - looser or tighter for each kind of limit, both ways;
  - the warnings, including values exactly at a limit;
  - a turned-off account (closing passes, flipping the position doesn't);
  - limits reaching everything that copied one;
  - a late account's loops starting and stopping with the platform;
  - the last-admin guard; turning off, unlocking and resetting;
  - account changes; an older users file;
  - the whole API, including a new user's first login and password change, and the stream refused before it.
- `context/scripts/mutate-admin.py`: 41/41 caught. Two mutants of the looser test are left out because they are equivalent: a change only ever holds values that differ. The first run found 5 real gaps, and each now has a test.
- `context/scripts/check-admin.py` on the demo, and again after a restart (`--after-restart`):
  - a new user logged in with the one-time password, was refused (403) until they set their own, then worked;
  - their new paper account ran at once and took an order;
  - after the preview and then the real change of the per-order limit to 1 lot, a 2-lot order was refused;
  - the turned-off account refused an opening order and cancelled a working one;
  - the demo admin couldn't turn itself off;
  - the audit rows were written;
  - after a restart, the account ran with its saved limit.

**Until K13.2:** the screens don't offer the password change yet. A user created from the API logs in, then sets their password with `POST /api/me/password`; K13.2 adds that screen.
