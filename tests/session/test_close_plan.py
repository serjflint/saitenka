from __future__ import annotations

import pytest

from saitenka.app.features.tooltip.preparation import TOOLTIP_PREPARATION_CLOSE_PARTICIPANTS
from saitenka.app.session.close_plan import CloseContributions, assemble_close_participants


def test_feature_close_contribution_cannot_replace_another_participant() -> None:
    def close() -> None:
        pass

    preparation = dict.fromkeys(TOOLTIP_PREPARATION_CLOSE_PARTICIPANTS, close)
    preparation["lanes:annotation"] = close
    contributions = CloseContributions(
        close_tts=close,
        close_anki=close,
        cancel_interaction_jobs=close,
        close_hover_metadata=close,
        start_lane_budget=close,
        close_lane=lambda _name, _remaining: None,
        lane_remaining=lambda: 0.0,
        close_annotation=close,
        close_tooltip_raster=close,
        close_tooltip_engaged=close,
        tooltip_preparation=preparation,
        close_analysis=close,
        close_render_pool=close,
    )

    with pytest.raises(RuntimeError, match="tooltip preparation close contribution mismatch"):
        assemble_close_participants(contributions)
