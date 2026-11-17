"""Bounded polling for read-only ROS services; never use for transitions or goals."""
import math


class ReadOnlyServicePoll:
    def __init__(self, timeout_s=2.0):
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError('A finite positive deadline is required')
        self.timeout_s = timeout_s
        self.future = None
        self.started = 0.0
        self.timeouts = 0

    def clear(self, client):
        if self.future is not None:
            client.remove_pending_request(self.future)
            if not self.future.done(): self.future.cancel()
        self.future = None

    def poll(self, client, request, now):
        if not math.isfinite(now): raise ValueError('Clock must be finite')
        if self.future is not None:
            if self.future.done():
                future = self.future
                self.future = None
                return future.result()
            if now - self.started < self.timeout_s: return None
            self.clear(client)
            self.timeouts += 1
        if client.service_is_ready():
            self.future = client.call_async(request)
            self.started = now
        return None
