# core/encoder.py
import time
from collections import deque


class ImprovedEncoderHandler:
    """Filters encoder steps (debounce, direction consistency, acceleration)."""
    def __init__(self, app):
        """Store app reference and initialise encoder filters."""
        self.app = app
        self._debounce_time_ms = 5
        self._min_detent_interval_ms = 10
        self._direction_lock_window_ms = 40
        self._opposite_threshold = 2

        self._last_detent_time = 0.0
        self._last_direction = 0
        self._opposite_count = 0
        self._direction_lock_until = 0.0
        self._direction_history = deque(maxlen=4)
        self._last_valid_direction = 0

        self._step_accumulator = 0
        self._acceleration_threshold = 4
        self._max_acceleration_steps = 3

    def _now_ms(self) -> float:
        """Return monotonic time in milliseconds."""
        return time.monotonic() * 1000.0

    def _is_direction_consistent(self) -> bool:
        """Check if recent detents mostly agree on direction."""
        hist = self._direction_history
        if len(hist) < 3:
            return True
        last = hist[-1]
        tail3 = list(hist)[-3:]
        same = sum(1 for d in tail3 if d == last)
        return same >= 2

    def process_encoder_step(self, sign: int) -> bool:
        """Apply debounce/locking to a single detent step; True if accepted."""
        now = self._now_ms()

        if now - self._last_detent_time < self._debounce_time_ms:
            return False

        if now - self._last_detent_time < self._min_detent_interval_ms:
            return False

        self._direction_history.append(sign)

        if not self._is_direction_consistent():
            return False

        current_direction = sign

        if current_direction == self._last_valid_direction:
            self._last_direction = current_direction
            self._opposite_count = 0
            self._direction_lock_until = now + self._direction_lock_window_ms
            self._last_detent_time = now
            self._last_valid_direction = current_direction
            return True

        if current_direction == -self._last_valid_direction:
            if now < self._direction_lock_until:
                self._opposite_count += 1
                if self._opposite_count < self._opposite_threshold:
                    return False

            self._last_direction = current_direction
            self._opposite_count = 0
            self._direction_lock_until = now + self._direction_lock_window_ms
            self._last_detent_time = now
            self._last_valid_direction = current_direction
            return True

        self._last_direction = current_direction
        self._opposite_count = 0
        self._direction_lock_until = now + self._direction_lock_window_ms
        self._last_detent_time = now
        self._last_valid_direction = current_direction
        return True

    def process_encoder_with_acceleration(self, sign: int) -> int:
        """Process step and return number of steps including acceleration."""
        if not self.process_encoder_step(sign):
            return 0

        self._step_accumulator += 1

        if self._step_accumulator <= 1:
            steps = 1
        elif self._step_accumulator <= 3:
            steps = 2
        else:
            steps = self._max_acceleration_steps

        if sign != self._last_valid_direction:
            self._step_accumulator = 1
            steps = 1

        return steps

    def reset_accumulator(self):
        """Reset acceleration accumulator (e.g., after inactivity)."""
        self._step_accumulator = 0
