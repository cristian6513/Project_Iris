"""F14 coordinator: optional mail work owned by Core, never by an open window."""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import re
import time

from core.google_connections import GoogleConnectionsService
from core.mail_classification_store import ClassificationStore, ClassificationError, Options
from core.mail_classifier_client import worker
from core.mail_classifier_worker import validate_result
from core.mail_service import project_message


class MailClassificationRuntime:
    def __init__(self, home: Path, *, google, hermes_home=None, invoke=worker,
                 clock=time.time, changed=lambda: None, functional_enabled=lambda: True, deadline_seconds=60):
        self.home, self._google, self._invoke, self._clock = home, google, invoke, clock
        self._hermes_home = hermes_home
        self._store = ClassificationStore.for_profile(home)
        self._changed, self._functional_enabled = changed, functional_enabled
        self._control, self._task, self._closed = asyncio.Lock(), None, False
        self._next = 0.0
        self.state = "off"
        self._active_id = None
        self.deadline_seconds = deadline_seconds

    async def _db(self, function, *args, **kwargs):
        return await GoogleConnectionsService._settle(asyncio.create_task(asyncio.to_thread(function, *args, **kwargs)))

    def _model_home(self):
        if self._hermes_home is None:
            from core.vision_model import profile_home
            self._hermes_home = profile_home(None).resolve()
        return self._hermes_home

    def _owner(self):
        owner = self._google()
        if owner is None or self._closed or not self._functional_enabled():
            raise ClassificationError("Correo no está disponible para clasificar.")
        return owner

    def _profile_id(self):
        return hashlib.sha256(os.path.normcase(str(self._model_home().resolve())).encode()).hexdigest()

    async def probe(self, provider, model):
        response = await self._invoke(self._model_home(), {"command": "probe", "provider": provider, "model": model})
        if not response.get("ok"):
            raise ClassificationError("No hay una ruta sin herramientas compatible con ese proveedor y modelo.")
        return response["route"]

    async def configure(self, options, *, route=None, confirmed=False):
        options = Options.model_validate(options).model_dump()
        ids = options["account_ids"]
        if len(set(ids)) != len(ids) or any(not re.fullmatch(r"gacc_[a-f0-9]{32}", key) for key in ids):
            raise ClassificationError("Selecciona cuentas válidas y sin repetir.")
        async with self._control:
            bindings, frontier, epochs = {}, 0, {}
            if options["enabled"]:
                if not ids or not confirmed:
                    raise ClassificationError("Selecciona cuentas y autoriza el envío expresamente.")
                actual = await self.probe(options["provider"], options["model"])
                if actual != route:
                    raise ClassificationError("El destino cambió. Compruébalo y revísalo otra vez.")
                snapshot = await self._owner().mail_classifier_snapshot(ids)
                bindings, frontier = snapshot["accounts"], snapshot["frontier"]
                epochs = snapshot["epochs"]
            settings = await self._db(self._store.configure, options, route=route or {},
                                      bindings=bindings, frontier=frontier, confirmed=confirmed,
                                      profile_id=self._profile_id() if options["enabled"] else "", epochs=epochs)
            await self._stop()
            self._next = 0
            self.state = "awaiting_baseline" if options["enabled"] else "off"
            self._changed()
            return settings

    async def _stop(self):
        task = self._task
        if task and not task.done():
            task.cancel()
        if task:
            await GoogleConnectionsService._settle(asyncio.create_task(GoogleConnectionsService._collect_mail([task])))
        self._task = None

    async def close(self):
        async with self._control:
            self._closed = True
            await self._stop()

    async def tick(self):
        async with self._control:
            if self._closed:
                return
            if not self._functional_enabled():
                await self._stop()
                self.state = "paused"
                return
            if self._task and not self._task.done():
                return
            if self._task:
                await self._stop()
            settings = await self._db(self._store.settings)
            options = settings["options"]
            if not options["enabled"]:
                self.state = "off"
                return
            if settings.get("profile_id") != self._profile_id():
                self.state = "profile_changed"
                return
            now = self._clock()
            if now < self._next:
                return
            self._next = now + options["interval_minutes"] * 60
            try:
                owner = self._owner()
                snapshot = await owner.mail_classifier_snapshot(options["account_ids"], settings["cursors"])
                if snapshot["accounts"] != settings["bindings"]:
                    self.state = "connection_changed"
                    return
                self.state = "waiting"
                waiting = set(options["account_ids"]) - set(settings.get("ready_accounts", []))
                initialized = set()
                if waiting:
                    self._next = now + 15
                for account in waiting:
                    if await self._db(self._store.establish_baseline, options["revision"], account,
                                      snapshot["epochs"][account], snapshot["frontier"]):
                        self._next = 0
                        initialized.add(account)
                if waiting - initialized:
                    self.state = "awaiting_baseline"
                await self._skip_backlog(options, settings, snapshot["messages"], waiting)
                for row in sorted(snapshot["messages"], key=lambda row: row["seq"]):
                    account = row["account_id"]
                    if account in waiting:
                        continue
                    if row["seq"] <= settings["cursors"].get(account, 0):
                        continue
                    message = project_message(account, row["message"])
                    if not message or not set(row["message"].get("labelIds", [])).intersection(options["folders"]):
                        await self._db(self._store.advance, options["revision"], account, row["seq"])
                        continue
                    # Register before any admission/send; disconnect owns cancellation.
                    binding = settings["bindings"][account]
                    self._task = await owner.start_mail_classification(account, binding["generation"], binding["revision"],
                        lambda row=row, message=message: self._classify(owner, settings, row, message))
                    self.state = "running"
                    return  # bounded: at most one model call per interval
            except asyncio.CancelledError:
                raise
            except Exception:
                self.state = "unavailable"

    async def _skip_backlog(self, options, settings, rows, waiting):
        """Newest first in arrival order (review 2026-10-19).

        One call per interval and a daily budget let a busy inbox fall days behind, so an
        «important» notice could arrive long after it mattered. Only the newest ``daily_limit``
        pending arrivals per account stay; older ones are passed over, never sent. Arrival
        order, not the sender's date, decides what is newest.
        """
        keep = max(1, options["daily_limit"])
        pending = {}
        for row in rows:
            account = row["account_id"]
            if account in waiting or row["seq"] <= settings["cursors"].get(account, 0):
                continue
            message = project_message(account, row["message"])
            if message and set(row["message"].get("labelIds", [])).intersection(options["folders"]):
                pending.setdefault(account, []).append(row["seq"])
        for account, sequences in pending.items():
            if len(sequences) > keep:
                last_skipped = sorted(sequences)[-keep - 1]
                await self._db(self._store.advance, options["revision"], account, last_skipped)
                settings["cursors"][account] = max(last_skipped, settings["cursors"].get(account, 0))

    async def _classify(self, owner, settings, row, message):
        try:
            async with asyncio.timeout(self.deadline_seconds):
                await self._perform_classification(owner, settings, row, message)
        except TimeoutError:
            self.state = "request_unknown"

    async def _perform_classification(self, owner, settings, row, message):
        job = None
        try:
            if settings.get("profile_id") != self._profile_id():
                raise ClassificationError("profile_changed")
            job, state = await self._db(self._store.reserve, settings, row, message, self._clock())
            if job is None:
                self.state = state
                return
            self._active_id = job["id"]
            payload = {key: message[key] for key in ("subject", "sender", "to", "snippet")}
            payload["body"] = ""
            if settings["options"]["content"] == "body":
                body = await owner.mail_body(row["account_id"], row["message"]["id"])
                payload["body"] = body["text"][:20000]
            # Recheck all grants after slow body reads and immediately before send.
            current = await self._db(self._store.settings)
            snapshot = await owner.mail_classifier_snapshot(settings["options"]["account_ids"])
            if (current != settings and current["options"]["revision"] != settings["options"]["revision"]
                    or snapshot["accounts"] != settings["bindings"] or self._google() is not owner
                    or not self._functional_enabled()):
                raise ClassificationError("changed")
            if not await self._db(owner.mail_classifier_target_live, row["account_id"], settings["bindings"][row["account_id"]], row["message"]["id"]):
                raise ClassificationError("changed")
            async with asyncio.timeout(self.deadline_seconds):
                response = await self._invoke(self._model_home(), {"command": "classify", **{
                    key: settings["options"][key] for key in ("provider", "model", "criteria")},
                    "expected": settings["route"], "message": payload})
            snapshot = await owner.mail_classifier_snapshot(settings["options"]["account_ids"])
            result, code = None, response.get("code", "request_unknown")
            if (snapshot["accounts"] != settings["bindings"] or self._google() is not owner
                    or not await self._db(owner.mail_classifier_target_live, row["account_id"], settings["bindings"][row["account_id"]], row["message"]["id"])):
                code = "connection_changed"
            elif response.get("ok") and response.get("route") == settings["route"]:
                result, code = validate_result(response["result"]), ""
                reported = response.get("reported_model", "")
                job["reported_model"] = reported if isinstance(reported, str) and len(reported) <= 200 else ""
            await self._db(self._store.finish, job, result, code, self._clock())
            self.state = "waiting" if result else code
        except asyncio.CancelledError:
            if job:
                await self._db(self._store.finish, job, None, "cancelled_unknown", self._clock())
            self.state = "cancelled_unknown"
            raise
        except Exception:
            if job:
                await self._db(self._store.finish, job, None, "request_unknown", self._clock())
            self.state = "request_unknown"
        finally:
            self._active_id = None
            self._changed()

    async def snapshot(self):
        settings = await self._db(self._store.settings)
        rows = await self._db(self._store.rows)
        try:
            current = await self._owner().mail_classifier_snapshot(settings["options"]["account_ids"])
            eligible = {key for key, binding in current["accounts"].items() if settings["bindings"].get(key) == binding}
        except Exception:
            eligible = set()
        public = []
        for row in rows:
            if (row["account_id"] in eligible and await self._db(self._owner().mail_classifier_target_live,
                    row["account_id"], settings["bindings"][row["account_id"]], row["remote_id"])):
                public.append({**{key: value for key, value in row.items() if key != "remote_id"},
                               "state": "running" if row["id"] == self._active_id else row["state"]})
            if len(public) >= 200:
                break
        return {"schema": "iris.mail-classification/v1", "options": settings["options"], "route": settings["route"],
                "state": self.state, "results": public,
                "daily_used": sum(time.gmtime(row["created_at"])[:3] == time.gmtime(self._clock())[:3] for row in rows)}

    async def correct(self, identity, label, revision):
        snapshot = await self.snapshot()
        if not any(row["id"] == identity for row in snapshot["results"]):
            raise ClassificationError("El resultado ya no está disponible en esta cuenta.")
        result = await self._db(self._store.correct, identity, label, revision, self._clock())
        self._changed()
        return {key: value for key, value in result.items() if key != "remote_id"}

    def inbox_rows(self):
        """Synchronous Inbox projection. Grants are checked now, never cached by the UI."""
        settings = self._store.settings()
        eligible = self._owner().mail_classifier_live(settings["bindings"])
        return [row for row in self._store.rows() if row["account_id"] in eligible and row["notification"]
                and self._owner().mail_classifier_target_live(row["account_id"], settings["bindings"][row["account_id"]], row["remote_id"])][:300]

    def act(self, identity, action, *, expected_revision, minutes=None):
        if not any(row["id"] == identity for row in self.inbox_rows()):
            raise ClassificationError("El aviso ya no está disponible.")
        row = self._store.act(identity, action, expected_revision, self._clock())
        self._changed()
        return row, True
