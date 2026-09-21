from __future__ import annotations


def draw_request(*, styles, boxes):
    from saitenka_tokenize.japanese import Token

    from saitenka.app.subtitle_render import DrawRequest

    surfaces = ["猫", "を", "見る"]
    tokens = [Token(s, s, s, "名詞", i, i + 1) for i, s in enumerate(surfaces)]
    return DrawRequest(
        text="".join(surfaces),
        lines=[tokens],
        osd=(1280, 720),
        sub_size=44,
        bg_opacity=150,
        bottom_margin=40,
        secondary_role=False,
        upgrade_pending=False,
        annotation_degraded=False,
        annotation_visible=True,
        hover=-1,
        hover_span=None,
        styles=styles,
        boxes=boxes,
    )


class Style:
    def __init__(self, color, underline=None) -> None:
        self.color = color
        self.underline = underline


class FakeSurfaces:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, object]] = []

    def present_rgba(self, rgba, x, y, *, oid, owner, on_settled=None) -> None:
        self.calls.append(("present", oid, (x, y, len(rgba.tobytes()), owner)))
        if on_settled is not None:
            on_settled(True)  # noqa: FBT003

    def remove(self, oid, *, owner, on_settled=None) -> None:
        self.calls.append(("remove", oid, owner))
        if on_settled is not None:
            on_settled(True)  # noqa: FBT003

    def overpaint_traffic(self) -> list[str]:
        from saitenka.app.overlay_ids import OverlayId

        return [kind for kind, oid, _ in self.calls if oid == OverlayId.OVERPAINT]
