"""F08b: a durable, single-attempt spoken summary after a visual return receipt.

No model call, server-speaker fallback, automatic replay, or implicit reading.
The outbox uses AutomationStore's hardened transaction; all rows share that
profile boundary and commit together. An attempt is ambiguous *before* TTS.
"""
from __future__ import annotations

import asyncio
import base64
import time
from typing import Annotated, Callable, Literal

from pydantic import Field

from core.automation_contracts import (
    AutomationContract, AutomationDelivery, AutomationPreferences, AutomationResult,
    AutomationRule, Identifier, InboxPreferences, SurfaceName, Timestamp, delivery_view, new_id,
)
from core.inbox_api import _excerpt, _quiet_hours
from core.speech_text import text_for_speech


class VoiceIntent(AutomationContract):
    id: Identifier
    token: Identifier
    presentation_id: Identifier
    surface_id: SurfaceName
    preference_revision: int
    rule_revisions: dict[str, int]
    results: Annotated[list[Identifier], Field(min_length=1, max_length=3)]
    text: Annotated[str, Field(min_length=1, max_length=500)]
    expires_at: Timestamp
    state: Literal['attempted', 'spoken', 'failed', 'ambiguous'] = 'attempted'


def _encoded(model):
    return model.model_dump_json(by_alias=True)


class InboxVoiceOutbox:
    def __init__(self, store, *, clock: Callable[[], float] = time.time):
        self.store, self.clock = store, clock

    @staticmethod
    def _preferences(conn):
        row = conn.execute("SELECT value FROM meta WHERE key='automation_preferences'").fetchone()
        return AutomationPreferences.model_validate_json(row[0]) if row else AutomationPreferences()

    @staticmethod
    def _quiet(conn, now):
        row = conn.execute("SELECT value FROM meta WHERE key='inbox_preferences'").fetchone()
        prefs = InboxPreferences.model_validate_json(row[0]) if row else InboxPreferences()
        return not prefs.return_notice or prefs.quiet_until > now

    @staticmethod
    def _item(conn, result_id):
        result_row = conn.execute('SELECT document FROM results WHERE id=?', (result_id,)).fetchone()
        delivery_row = conn.execute('SELECT document FROM deliveries WHERE result_id=?', (result_id,)).fetchone()
        if not result_row or not delivery_row:
            return None
        result = AutomationResult.model_validate_json(result_row[0])
        rule_row = conn.execute('SELECT document FROM rules WHERE id=?', (result.rule_id,)).fetchone()
        if not rule_row:
            return None
        return result, AutomationDelivery.model_validate_json(delivery_row[0]), AutomationRule.model_validate_json(rule_row[0])

    def begin(self, payload, *, surface_id):
        now = self.clock()
        with self.store._transaction() as conn:
            prefs = self._preferences(conn)
            if not prefs.return_voice or prefs.focus or self._quiet(conn, now):
                return None
            presentation = self.store._presentation(conn, str(payload.get('id', '')))
            if (presentation is None or presentation.state != 'shown' or presentation.surface_id != surface_id
                    or presentation.token != payload.get('token') or type(payload.get('generation')) is not int
                    or presentation.generation != payload['generation'] or now > presentation.lease_expires_at):
                return None
            key = 'voice_presentation:' + presentation.id
            if conn.execute('SELECT 1 FROM meta WHERE key=?', (key,)).fetchone():
                return None
            items, lines, revisions = [], [], {}
            for result_id in presentation.results:
                item = self._item(conn, result_id)
                if item is None:
                    continue
                result, delivery, rule = item
                if (not rule.delivery.voice or _quiet_hours(rule, now) or delivery.voice_attempts
                        or delivery_view(delivery, now) != 'presented' or delivery.visual_receipt is None
                        or delivery.visual_receipt.presentation_id != presentation.id):
                    continue
                items.append((result, delivery))
                revisions[rule.id] = rule.revision
                outcome = 'terminó' if result.status == 'completed' else 'falló'
                lines.append(f'{rule.title}: {outcome}. {text_for_speech(_excerpt(result.text))[:180]}')
                if len(items) == 3:
                    break
            if not items:
                return None
            intent = VoiceIntent(id=new_id(), token=new_id(), presentation_id=presentation.id, surface_id=surface_id,
                preference_revision=prefs.revision, rule_revisions=revisions,
                results=[result.id for result, _ in items],
                text=text_for_speech('En tu bandeja. ' + ' '.join(lines))[:500], expires_at=now + 60)
            for result, delivery in items:
                updated = AutomationDelivery.model_validate(delivery.model_dump(by_alias=True) | {
                    'revision':delivery.revision+1, 'voice_attempts':1, 'voice_receipt':'ambiguous'})
                conn.execute('UPDATE deliveries SET document=? WHERE result_id=?', (_encoded(updated),result.id))
            conn.execute('INSERT INTO meta VALUES (?,?)', (key, intent.id))
            conn.execute('INSERT INTO meta VALUES (?,?)', ('voice_intent:'+intent.id, _encoded(intent)))
            self.store._history(conn, now, 'voice_attempt', intent.id, {})
        self.store._notify('voice')
        return intent

    def allowed(self, intent):
        now = self.clock()
        with self.store._read() as conn:
            prefs = self._preferences(conn)
            if (not prefs.return_voice or prefs.focus or prefs.revision != intent.preference_revision
                    or now >= intent.expires_at or self._quiet(conn,now)):
                return False
            for result_id in intent.results:
                item = self._item(conn,result_id)
                if item is None:
                    return False
                _, delivery, rule = item
                if (not rule.delivery.voice or rule.revision != intent.rule_revisions.get(rule.id)
                        or _quiet_hours(rule,now) or delivery_view(delivery,now) != 'presented'):
                    return False
            return True

    def finish(self, intent_id, *, token, surface_id, outcome):
        if not isinstance(outcome,str) or outcome not in {'spoken','failed','ambiguous'}:
            return False
        now = self.clock()
        with self.store._transaction() as conn:
            key = 'voice_intent:' + str(intent_id)
            row = conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            if row is None:
                return False
            intent = VoiceIntent.model_validate_json(row[0])
            if (intent.state != 'attempted' or intent.token != token or intent.surface_id != surface_id
                    or now > intent.expires_at):
                return False
            updated = intent.model_copy(update={'state':outcome})
            conn.execute('UPDATE meta SET value=? WHERE key=?', (_encoded(updated),key))
            for result_id in intent.results:
                row = conn.execute('SELECT document FROM deliveries WHERE result_id=?',(result_id,)).fetchone()
                if row is None:
                    continue
                delivery = AutomationDelivery.model_validate_json(row[0])
                delivery = AutomationDelivery.model_validate(delivery.model_dump(by_alias=True) | {
                    'revision':delivery.revision+1,'voice_receipt':outcome})
                conn.execute('UPDATE deliveries SET document=? WHERE result_id=?',(_encoded(delivery),result_id))
            self.store._history(conn,now,'voice_'+outcome,intent.id,{})
        self.store._notify('voice')
        return True


async def _settled(function, *args, **kwargs):
    task = asyncio.create_task(asyncio.to_thread(function,*args,**kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class InboxVoiceService:
    """Injected transport/TTS; never plays locally. One utterance across surfaces."""
    def __init__(self, *, store, eligible, synthesize, clock=time.time):
        self.store, self.eligible, self.synthesize, self.clock = store, eligible, synthesize, clock
        self._lock = asyncio.Lock()
        self._active = {}
        self._tasks = {}

    async def offer(self, client, surface_id, presentation):
        for owner, active in list(self._active.items()):
            if self.clock() >= active.expires_at:
                self._active.pop(owner,None)
        if self._lock.locked() or self._active or not self.eligible(client):
            return
        async with self._lock:
            store = self.store()
            if store is None or not self.eligible(client):
                return
            outbox = InboxVoiceOutbox(store,clock=self.clock)
            task = asyncio.current_task()
            self._tasks[client] = task
            try:
                intent = await _settled(outbox.begin,presentation,surface_id=surface_id)
                if intent is None:
                    return
                self._active[client] = intent
                if not self.eligible(client) or not await _settled(outbox.allowed,intent):
                    await _settled(outbox.finish,intent.id,token=intent.token,surface_id=surface_id,outcome='failed')
                    self._active.pop(client,None)
                    return
                audio, visemes = await asyncio.wait_for(self.synthesize(intent.text), 15)
                if (not audio or audio == b'FALLBACK_FAILED' or len(audio) > 8*1024*1024
                        or not self.eligible(client) or not await _settled(outbox.allowed,intent)):
                    await _settled(outbox.finish,intent.id,token=intent.token,surface_id=surface_id,outcome='failed')
                    self._active.pop(client,None)
                    return
                mime = 'audio/wav' if audio.startswith(b'RIFF') else 'audio/ogg' if audio.startswith(b'OggS') else 'audio/mpeg'
                # The durable ambiguous attempt already exists. A lost send is never retried.
                await asyncio.wait_for(client.send_json({'type':'inbox_voice','payload':{
                    'id':intent.id,'token':intent.token,'surface_id':surface_id,'expires_at':intent.expires_at,
                    'text':intent.text,'audio':base64.b64encode(audio).decode('ascii'),'mime':mime,
                    'visemes':visemes}}),2)
            except asyncio.CancelledError:
                raise
            except Exception:
                # TTS/send might have progressed. Leave the durable intent ambiguous.
                return
            finally:
                if self._tasks.get(client) is task:
                    self._tasks.pop(client,None)

    async def receipt(self, client, surface_id, payload):
        intent = self._active.get(client)
        if (intent is None or payload.get('id') != intent.id or payload.get('token') != intent.token
                or surface_id != intent.surface_id):
            return False
        store = self.store()
        if store is None:
            return False
        accepted = await _settled(InboxVoiceOutbox(store,clock=self.clock).finish, intent.id,
            token=intent.token,surface_id=surface_id,outcome=payload.get('outcome'))
        if accepted:
            self._active.pop(client,None)
        return accepted

    async def stop(self, owner=None):
        tasks = [task for client,task in self._tasks.items() if (owner is None or client is owner)
                 and task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        for client in list(self._active):
            if owner is not None and client is not owner:
                continue
            try:
                await asyncio.wait_for(client.send_json({'type':'inbox_voice_stop','payload':{}}),1)
            except Exception:
                pass
            self._active.pop(client,None)
        if tasks:
            await asyncio.gather(*tasks,return_exceptions=True)
