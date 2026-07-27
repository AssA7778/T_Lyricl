from .base import Source


def build(kind: str, cfg: dict, clock):
    kind = (kind or "webhook").lower()
    if kind == "webhook":
        from .webhook import WebhookSource

        return WebhookSource(clock, **cfg)
    if kind == "spotify":
        from .spotify import SpotifySource

        return SpotifySource(clock, **cfg)
    if kind == "mpris":
        from .mpris import MprisSource

        return MprisSource(clock, **cfg)
    raise ValueError(f"منبع ناشناخته: {kind!r} (webhook | spotify | mpris)")
