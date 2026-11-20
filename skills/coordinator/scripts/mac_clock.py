"""The clock a fleet watcher reads, which tells time the Mac spent awake from time it slept."""
import time

SECONDS_ASLEEP_THAT_COUNT_AS_A_SLEEP = 30


class MacClock:
    """The time, and how long this Mac has stayed awake since an earlier time.

    time.monotonic() stops while the Mac sleeps and time.time() keeps
    running, so wall time that passed with no monotonic time is a sleep.
    time.monotonic() starts again from zero when the Mac restarts, so a
    lower reading than the last is a restart. Both count as a wake.

    last_reading is the reading an earlier watcher saved. It carries the
    last wake that watcher saw, and shows a sleep between the two watchers.
    """

    def __init__(self, last_reading=None):
        try:
            self.wall, self.awake, *saved_wake = (float(value) for value in last_reading)
        except (TypeError, ValueError):
            self.wall, self.awake, saved_wake = time.time(), time.monotonic(), []
        self.mac_woke_at = saved_wake[0] if saved_wake else 0
        self.read()

    def read(self):
        wall, awake = time.time(), time.monotonic()
        seconds_asleep = (wall - self.wall) - (awake - self.awake)
        if awake < self.awake or seconds_asleep >= SECONDS_ASLEEP_THAT_COUNT_AS_A_SLEEP:
            self.mac_woke_at = wall
        self.wall, self.awake = wall, awake
        return wall

    def reading(self):
        return [self.wall, self.awake, self.mac_woke_at]

    def awake_seconds_since(self, wall_time):
        """Seconds since wall_time, or since the Mac last woke when that is later."""
        now = self.read()
        return now - max(wall_time, self.mac_woke_at)
