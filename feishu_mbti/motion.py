"""Predict chat scrolling from wheel input.

Feishu's accessibility tree reports new name positions only after the smooth
scroll animation ends (~150-300 ms). Wheel notches are seen immediately, and one
notch scrolls a fixed distance, so labels can move with the text instead.
"""

# Fitted to per-frame screen captures of Feishu (1-4 notches, 45-100 ms apart):
# motion starts ~15 ms after a notch and eases in and out over 160 ms. A notch
# arriving mid-animation re-aims it, keeping its speed, and lands 120 ms later.
EASE_DELAY = .015
EASE_SECONDS = .16
RETARGET_SECONDS = .12
# Feishu draws its own frame before a moved label window is composed; leading
# by about a frame keeps labels level with the text at full speed.
LEAD_SECONDS = .006
# Away from the bottom the list drops ~17 DIP of bottom padding, so it can
# scroll that much further down than its accessibility box suggests.
BOTTOM_PADDING = 17
QUIET_SECONDS = .35  # After this, the accessibility positions are final.


def _curve(progress, slope):
    """Cubic from 0 to 1 with a starting slope and a gentle stop."""
    t = min(1.0, max(0.0, progress))
    return (t ** 3 - 2 * t ** 2 + t) * slope + (3 * t ** 2 - 2 * t ** 3)


def _speed(progress, slope):
    t = min(1.0, max(0.0, progress))
    return (3 * t ** 2 - 4 * t + 1) * slope + (6 * t - 6 * t ** 2)


def ease(progress):
    return _curve(progress, 0)


class WheelPredictor:
    def __init__(self, scale=1.0):
        self.ratio = 1.0  # Learned correction to the notch distance.
        self.scale = scale

    def notch_pixels(self):
        # Measured: 100 DIP per 120-unit notch in Feishu's message list.
        return 100 * self.scale * self.ratio

    def offset(self, wheels, now, low, high):
        """Current (visual offset, final target) in pixels; positive moves text down.

        low/high bound the target so the list never scrolls past its ends.
        """
        total = 0.0
        segment = None  # (start, from, to, duration, slope)
        for when, delta in wheels:
            total = min(high, max(low, total + delta / 120 * self.notch_pixels()))
            start = when + EASE_DELAY
            if start > now:
                continue  # Counted in the target; motion has not begun yet.
            origin, velocity, duration = 0.0, 0.0, EASE_SECONDS
            if segment is not None:
                begun, frm, to, length, slope = segment
                progress = (start - begun) / length
                origin = frm + (to - frm) * _curve(progress, slope)
                if progress < 1:
                    velocity = (to - frm) * _speed(progress, slope) / length
                    duration = RETARGET_SECONDS
            distance = total - origin
            slope = min(3.0, max(0.0, velocity * duration / distance)) if distance else 0.0
            segment = (start, origin, total, duration, slope)
        if segment is None:
            return 0.0, total
        begun, frm, to, length, slope = segment
        return frm + (to - frm) * _curve((now - begun) / length, slope), total

    def calibrate(self, predicted, actual, low, high):
        # Learn only from unclamped scrolls; a clamp hides the true distance.
        if abs(predicted) < 20 or not low < predicted < high:
            return
        self.ratio = min(2.0, max(.5, self.ratio * actual / predicted))


def predicted_positions(tracked, current, wheels, now, predictor):
    """Label positions for this frame.

    tracked: scan-time state with 'base' [(x, y, height)], 'band' (top, bottom)
    and 'content' (top, bottom) of the scrolled list, or None.
    current: live accessibility positions [(x, y, height) or None].
    wheels: [(time, delta)] since the scan.
    """
    top, bottom = tracked['band']
    use_prediction = bool(wheels) and tracked.get('content') is not None
    if use_prediction:
        content_top, content_bottom = tracked['content']
        gap = content_bottom - bottom
        # Already at the bottom: scrolling down changes nothing.
        low = 0.0 if gap <= 2 else -(gap + BOTTOM_PADDING * tracked.get('scale', 1))
        high = max(0.0, top - content_top)
        offset, target = predictor.offset(wheels, now + LEAD_SECONDS, low, high)
        if now - wheels[-1][0] >= QUIET_SECONDS:
            live = next(((c[1] - b[1]) for b, c in zip(tracked['base'], current) if c), None)
            if live is not None and abs(live - target) > 2:
                predictor.calibrate(target, live, low, high)
                use_prediction = False  # Trust the settled positions.
    positions = []
    for index, (base, live) in enumerate(zip(tracked['base'], current)):
        if use_prediction:
            x, y, height = base[0], base[1] + offset, base[2]
        elif live is not None:
            x, y, height = live
        else:
            positions.append({'index': index, 'visible': False})
            continue
        visible = height > 0 and top <= y and y + height <= bottom
        positions.append({'index': index, 'x': x, 'y': round(y), 'height': height, 'visible': visible})
    return positions
