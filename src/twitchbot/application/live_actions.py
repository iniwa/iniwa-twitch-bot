"""Explicit user actions. Construction and GET queries never dispatch adapters."""

from datetime import datetime

from .control import ActionResult, ChannelUpdate, UnavailableTwitchControl


class LiveActions:
    def __init__(self, repository, live_provider, *, adapter=None, runtime_allowed=None):
        self.repository = repository
        self.live_provider = live_provider
        self.adapter = adapter if adapter is not None else UnavailableTwitchControl()
        self.runtime_allowed = runtime_allowed if runtime_allowed is not None else lambda: False

    def _dispatch(self, operation, call, *, marker=False, claim=None):
        key = operation["id"]
        if operation["state"] != "pending":
            return operation
        if not self.adapter.available:
            self.repository.transition(key,"pending","unavailable",code="adapter_unavailable")
            return self.repository.operation(key)
        if not self.runtime_allowed():
            self.repository.transition(key,"pending","failed",code="runtime_stopped")
            return self.repository.operation(key)
        if not (claim() if claim else self.repository.transition(key,"pending","dispatching",code="dispatching")):
            return self.repository.operation(key)
        try:
            result = call()
            if not isinstance(result, ActionResult) or (marker and result.state == "succeeded" and (result.remote_id is None or result.position_seconds is None)):
                result = ActionResult("unknown")
        except Exception:
            # Do not echo provider error text or retry a potentially completed write.
            result = ActionResult("unknown")
        self.repository.transition(key,"dispatching",result.state,code=result.state,remote_id=result.remote_id,position=result.position_seconds)
        return self.repository.operation(key)

    def note(self, stream_id, body, request_id, *, marker=False):
        note, created = self.repository.create_note(request_id,stream_id,body,marker=marker)
        if not marker:
            return {"note": note, "operation": None}
        operation = self.repository.operation(request_id)
        if created:
            snapshot = self.live_provider.snapshot().stream
            observed = datetime.fromisoformat(snapshot.observed_at.replace("Z","+00:00"))
            age = (self.repository.clock()-observed).total_seconds()
            if snapshot.state != "live" or snapshot.id != stream_id or snapshot.stale or not 0<=age<=60:
                self.repository.transition(request_id,"pending","failed",code="live_state_unconfirmed")
                operation = self.repository.operation(request_id)
            else:
                operation = self._dispatch(operation,lambda:self.adapter.create_marker(self.repository.channel_id,body),marker=True)
        return {"note": note,"operation": operation}

    def apply_preset(self, preview_id, request_id):
        operation, preset, created = self.repository.accept_preset(preview_id,request_id)
        if created:
            operation = self._dispatch(operation,lambda:self.adapter.apply_preset(self.repository.channel_id,ChannelUpdate(preset.title,preset.game_id,preset.tags)),claim=lambda:self.repository.claim_preset(operation["id"]))
        return operation
